"""Feature Pipeline – aggregates raw signals into daily feature vectors.

The heart of the ML data preparation. For each point-in-time universe
ticker on a given NYSE session, computes ~90 features across 16 signal
groups and stores them in the feature_snapshots table via UPSERT.

Feature Groups:
  - ARK (11): ETF presence, weights, deltas, temporal trends
  - Insider (10): Net buys, cluster activity, temporal patterns, buy ratio
  - Analyst (7): Rating scores, upgrades, price targets, sentiment
  - Politician (4): Buy counts + distinct politicians (dual-date)
  - 13F (4): Top holder count, new/exited positions, holder delta
  - Fundamentals (8): Valuation ratios, margins, temporal trends
  - Technical (6): Price vs SMA, RSI, volume ratio, ATR
  - Liquidity (2): Dollar volume 20d, Amihud illiquidity (Sprint 9.5b E4)
  - Earnings (5): Days until, beats, surprise trend, SUE, PEAD (Sprint 9.5b B2)
  - Sentiment (7): News sentiment, momentum, attention, volume ratio (Sprint 9.5b E3)
  - Macro (6): VIX, yields, HY spread, dollar, inflation (Sprint 9.5b D1)
  - Breadth (2), Sector-relative (2), Short volume (3)
  - Options IV (4): ATM IV, skew, term slope, put/call OI (Sprint 9.5b D3)
  - Estimates (5): EPS/revenue revision %, net revision counts (Sprint 9.5a B1)

Point-in-time contract (FEATURE_VERSION 2026.10-1, review 2026-10)
-----------------------------------------------------------------
A snapshot for session ``d`` may only use information that was public at
the **close of d (16:00 America/New_York)**. Targets are measured from the
next session's open (see target_backfill.py). Concretely:

  * Non-trading days produce no rows (H3).
  * Insider: trades count from their ``filing_date``; clusters are rebuilt
    as of d from trades filed by d (C2) – the stored insider_clusters
    table is NOT used here.
  * Politicians: every query requires ``disclosure_date <= d`` (C3).
  * News: ``published_at`` (naive UTC, parsed from Alpaca ``...Z``
    timestamps) strictly before 16:00 ET on d; a single sentiment
    ``model_version`` (``SENTIMENT_MODEL_VERSION``) is used (C4, M9).
  * 13F: ``filing_date <= d``. SEC filings without acceptance timestamp
    filed on d itself may have been published after the close – accepted,
    documented residual risk.
  * Macro (FRED): daily series use observations up to the previous NYSE
    session (value of d is published the next day); DTWEXBGS (published
    weekly) uses a 7-day lag (H8). Live runs and recomputes see the same.
  * Earnings dates: ``first_seen <= d`` when the collector provides it
    (M2); legacy rows without ``first_seen`` and rows first seen only
    after the earnings date (retroactive) keep a documented lookahead.
  * Prices flagged ``is_extrapolated`` are never used (H7).
  * Forward-fill of snapshot-type sources is bounded (M3):
    ``MAX_SNAPSHOT_AGE_DAYS`` for fundamentals/estimates/IV/TA/price
    targets, ``FORM13F_MAX_AGE_DAYS`` for 13F report periods.
  * ``Universe.sector`` (for sector-relative features) is the CURRENT
    sector, not point-in-time (documented limitation).

NULL vs 0 (H5): counts/streaks are stored as 0 when the source covers the
ticker (e.g. the ticker has any insider filing / analyst rating /
politician trade / earnings history up to d) and NULL only without
coverage. Numeric values of 0.0 are never turned into NULL.

Every row stores ``feature_version`` and a fresh ``computed_at``; an
UPSERT overwrites ALL feature columns, including with NULL (H1).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from trading_signals.db.models.ark import ARKHolding
from trading_signals.db.models.estimates import EstimatesSnapshot
from trading_signals.db.models.features import FEATURE_COLUMNS, FeatureSnapshot
from trading_signals.db.models.form13f import Form13FHolding
from trading_signals.db.models.fundamentals import (
    AnalystRating,
    EarningsCalendar,
    FundamentalsSnapshot,
)
from trading_signals.db.models.insider import InsiderTrade
from trading_signals.db.models.macro_series import MacroSeries
from trading_signals.db.models.news import NewsSentiment
from trading_signals.db.models.options_iv import OptionsIVSnapshot
from trading_signals.db.models.politicians import PoliticianTrade
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.technical_indicators import TechnicalIndicator
from trading_signals.db.models.universe import Universe
from trading_signals.derived.insider_clusters import (
    CLUSTER_WINDOW_DAYS,
    FALLBACK_FILING_LAG_DAYS,
    find_clusters,
)
from trading_signals.utils.logging import get_logger
from trading_signals.utils.market_calendar import (
    is_trading_day,
    previous_trading_day,
    trading_days,
)
from trading_signals.utils.retention import data_start_date

logger = get_logger(__name__)

#: Version of the feature definitions; stored in every row. Bump whenever
#: the semantics of any feature (or of the targets) change.
FEATURE_VERSION = "2026.10-1"

#: News sentiment model whose scores feed the features (M9). Must match
#: ``SentimentScorer.model_version`` of the active scorer.
SENTIMENT_MODEL_VERSION = "finbert-v1"

#: Max age (calendar days ≈ 10 sessions) of snapshot-type sources (M3).
MAX_SNAPSHOT_AGE_DAYS = 14
#: Max age of the latest 13F report period (1 quarter + 60 days, M3).
FORM13F_MAX_AGE_DAYS = 92 + 60
#: Max age of an ARK ETF holdings snapshot before the ETF counts as stale.
ARK_MAX_AGE_DAYS = 10
#: Sessions of ARK history used for deltas / trend (needs >= 20).
ARK_LOOKBACK_SESSIONS = 20

#: FRED series published with a 1-session lag (value of d known on d+1).
DAILY_MACRO_SERIES = ("DGS2", "DGS10", "VIXCLS", "BAMLH0A0HYM2", "T10YIE")
#: FRED series published weekly – calendar-day publication lag.
WEEKLY_MACRO_LAG_DAYS = {"DTWEXBGS": 7}
MACRO_MAX_AGE_DAYS = 14

NY_TZ = ZoneInfo("America/New_York")
MARKET_CLOSE_ET = time(16, 0)

# Rating action -> numeric score mapping for analyst signals
_RATING_SCORES = {
    "up": 1.0,
    "main": 0.5,
    "reit": 0.3,
    "init": 0.0,
    "down": -1.0,
}

# B4 Sprint 9.5b: yfinance sector name -> GICS Sector SPDR ETF ticker
# Maps the sector names from Universe.sector (populated by yfinance/sector
# enrichment) to the corresponding Select Sector SPDR ETFs (from B3).
_SECTOR_ETF_MAP = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Communication Services": "XLC",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Energy": "XLE",
    # GICS names (from seed_benchmark_etfs, edge cases)
    "Information Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Staples": "XLP",
    "Consumer Discretionary": "XLY",
    "Materials": "XLB",
}


# ── Pure helpers (unit-tested) ───────────────────────────────────────


def _f(value) -> float | None:
    """Convert to float, keeping 0.0 (never truthiness-based)."""
    return float(value) if value is not None else None


def _not_extrapolated(col=PriceDaily.is_extrapolated):
    """SQL filter for real (non gap-filled) price rows; NULL counts as real."""
    return col.is_not(True)


def _slope(values) -> float | None:
    """OLS slope over index positions, ignoring None values."""
    clean = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    if len(clean) < 2:
        return None
    n = len(clean)
    x_m = sum(x for x, _ in clean) / n
    y_m = sum(y for _, y in clean) / n
    num = sum((x - x_m) * (y - y_m) for x, y in clean)
    den = sum((x - x_m) ** 2 for x, _ in clean)
    return round(num / den, 6) if den else None


def news_cutoff_utc(d: date) -> datetime:
    """16:00 America/New_York on ``d`` as a timezone-aware UTC datetime.

    Since migration 031 ``news_articles.published_at`` is ``timestamptz``
    (mapped via ``TZDateTime``, which interprets *naive* bind values as
    Europe/Berlin). The cutoff therefore MUST be timezone-aware so the
    comparison is an absolute point in time. All window bounds compared
    against ``published_at`` must be built with this helper.
    """
    return datetime.combine(d, MARKET_CLOSE_ET, tzinfo=NY_TZ).astimezone(UTC)


def ark_state_at(
    holdings: list[tuple],
    as_of: date,
    max_age_days: int = ARK_MAX_AGE_DAYS,
) -> dict[str, dict[str, tuple[float, float | None]]]:
    """ARK positions as of ``as_of`` (pure function, review finding H2).

    For every ETF the latest snapshot with ``snapshot_date <= as_of`` (and
    not older than ``max_age_days``) is used; a ticker no longer contained
    in that snapshot is NOT held, even if an older snapshot still lists it.

    Args:
        holdings: rows ``(snapshot_date, etf_ticker, ticker, weight_pct, shares)``.

    Returns:
        ``{ticker: {etf: (weight_pct, shares)}}``.
    """
    latest: dict[str, date] = {}
    for snap, etf, *_ in holdings:
        if snap <= as_of and snap >= as_of - timedelta(days=max_age_days):
            if etf not in latest or snap > latest[etf]:
                latest[etf] = snap
    state: dict[str, dict[str, tuple[float, float | None]]] = {}
    for snap, etf, ticker, weight, shares in holdings:
        if latest.get(etf) == snap:
            state.setdefault(ticker, {})[etf] = (
                float(weight) if weight is not None else 0.0,
                float(shares) if shares is not None else None,
            )
    return state


def compute_ark_features(
    ticker: str,
    states: list[dict],
    session_dates: list[date],
) -> dict:
    """ARK features from point-in-time states (pure function).

    Args:
        states: ``states[k]`` = :func:`ark_state_at` for the session ``k``
            sessions before d (``states[0]`` = d).
        session_dates: matching session dates (``session_dates[0]`` = d).

    Weight deltas are computed from the snapshots for all positions
    (new = +weight, closed = -previous weight, M6). Increase days and the
    conviction streak count distinct sessions, not per-ETF rows (LOW).
    Returns ``{}`` if the ticker was not held at any of the sessions.
    """
    if not states or not any(ticker in s for s in states):
        return {}
    d = session_dates[0]

    def _tw(k: int) -> float | None:
        if k >= len(states):
            return None
        return sum(w for w, _ in states[k].get(ticker, {}).values())

    held = states[0].get(ticker, {})
    etf_count = len(held)
    total_weight = _tw(0) or 0.0

    def _delta(k: int) -> float | None:
        prev = _tw(k)
        return round(total_weight - prev, 4) if prev is not None else None

    # Per-session classification: +1 increase day, -1 decrease day, 0 none
    def _day_type(k: int) -> int:
        if k + 1 >= len(states):
            return 0
        cur, prev = states[k].get(ticker, {}), states[k + 1].get(ticker, {})
        up = down = False
        for etf in set(cur) | set(prev):
            c = cur.get(etf, (0.0, 0.0))[1]
            p = prev.get(etf, (0.0, 0.0))[1]
            if etf in cur and etf in prev and c is not None and p is not None:
                if c > p:
                    up = True
                elif c < p:
                    down = True
            elif etf in prev and etf not in cur:
                down = True  # closed position
        if up and not down:
            return 1
        if down:
            return -1
        return 0

    day_types = [_day_type(k) for k in range(len(states) - 1)]

    def _increase_days(cal_days: int) -> int:
        since = d - timedelta(days=cal_days)
        return sum(
            1 for k, t in enumerate(day_types) if t == 1 and session_dates[k] >= since
        )

    # Streak over the most recent activity days (days with a change)
    streak = 0
    for t in day_types:
        if t == 0:
            continue
        if t == 1:
            streak += 1
        else:
            break

    # Weight trend: slope of total weight over the last 21 sessions
    weights = [_tw(k) for k in range(len(states) - 1, -1, -1)]
    present = sum(1 for k in range(len(states)) if ticker in states[k])
    weight_trend = _slope(weights) if present >= 3 else None

    return {
        "ark_in_etf_count": etf_count,
        "ark_total_weight": round(total_weight, 4),
        "ark_weight_delta_1d": _delta(1),
        "ark_weight_delta_5d": _delta(5),
        "ark_weight_delta_20d": _delta(20),
        "ark_conviction_score": round(total_weight * etf_count, 4),
        "ark_multi_etf_signal": etf_count >= 2,
        "ark_increase_days_10d": _increase_days(10),
        "ark_increase_days_20d": _increase_days(20),
        "ark_conviction_streak": streak,
        "ark_weight_trend_20d": weight_trend,
    }


def insider_cluster_features(purchases: list, d: date) -> dict:
    """Cluster features as of ``d`` from purchases public by ``d`` (C2).

    ``purchases`` must already be restricted to trades with
    ``filing_date <= d``. Since every member trade was filed by d, every
    cluster found here has ``known_date <= d``.

    ``insider_cluster_active`` means the most recent cluster's window is
    still open (``cluster_end >= d - CLUSTER_WINDOW_DAYS``).
    """
    clusters = find_clusters(purchases)
    since_30 = d - timedelta(days=30)
    since_60 = d - timedelta(days=60)
    active = [
        c
        for c in clusters
        if c["cluster_end"] >= d - timedelta(days=CLUSTER_WINDOW_DAYS)
    ]
    latest_active = max(active, key=lambda c: c["cluster_end"]) if active else None
    in_30 = [c for c in clusters if since_30 <= c["cluster_start"] <= d]
    in_60 = [c for c in clusters if since_60 <= c["cluster_start"] <= d]
    last_end = max((c["cluster_end"] for c in clusters), default=None)
    return {
        "insider_cluster_active": latest_active is not None,
        "insider_cluster_score": (
            float(latest_active["score"]) if latest_active is not None else None
        ),
        "cluster_count_30d": len(in_30),
        "cluster_count_60d": len(in_60),
        "cluster_score_sum_60d": round(sum(float(c["score"]) for c in in_60), 4),
        "days_since_last_cluster": (d - last_end).days if last_end else None,
    }


def insider_trade_features(trades: list[tuple], d: date) -> dict:
    """Net-buy / value / ratio features from (type, filing_date, value) rows.

    Windows are on ``filing_date`` (public availability, LOW finding).
    Counts are 0 (not NULL) – the caller decides coverage.
    """
    since_30 = d - timedelta(days=30)
    since_90 = d - timedelta(days=90)

    def _win(since: date, typ: str) -> list[float]:
        return [
            float(v) if v is not None else 0.0
            for t, fd, v in trades
            if t == typ and fd is not None and since <= fd <= d
        ]

    buys_30, sells_30 = _win(since_30, "P"), _win(since_30, "S")

    def _ratio(since: date) -> float | None:
        b, s = sum(_win(since, "P")), sum(_win(since, "S"))
        return round(b / (b + s), 4) if (b + s) > 0 else None

    return {
        "insider_net_buy_count_30d": len(buys_30) - len(sells_30),
        "insider_buy_value_30d": round(sum(buys_30), 2),
        "insider_buy_ratio_30d": _ratio(since_30),
        "insider_buy_ratio_90d": _ratio(since_90),
    }


def liquidity_from_prices(rows: list[tuple]) -> dict:
    """Dollar volume (mean of close*volume) and Amihud illiquidity.

    ``rows`` = chronologically sorted ``(close, volume)`` of real sessions.
    """
    if len(rows) < 5:
        return {}
    dollar_vols = [
        float(c) * int(v)
        for c, v in rows
        if c is not None and v is not None and float(c) > 0 and int(v) > 0
    ]
    dollar_volume = (
        round(sum(dollar_vols) / len(dollar_vols), 0) if dollar_vols else None
    )
    amihud_vals = []
    for (pc, _), (cc, cv) in zip(rows, rows[1:]):
        if pc is None or cc is None or cv is None:
            continue
        pc, cc = float(pc), float(cc)
        dv = cc * int(cv)
        if pc > 0 and dv > 0:
            amihud_vals.append(abs(cc / pc - 1) / dv)
    amihud = (
        # Scale by 1e6 for readability (Amihud values are tiny)
        round(sum(amihud_vals) / len(amihud_vals) * 1e6, 6) if amihud_vals else None
    )
    return {"dollar_volume_20d": dollar_volume, "amihud_illiquidity_20d": amihud}


def earnings_history_features(past: list[tuple], d: date) -> dict:
    """Beat streak, surprise trend, SUE, days since last earnings.

    ``past`` = rows ``(earnings_date, eps_estimate, eps_actual, surprise_pct)``
    sorted newest first, all with ``earnings_date < d``.
    """
    if not past:
        return {
            "consecutive_beats": None,
            "surprise_trend_3q": None,
            "sue_last": None,
            "days_since_last_earnings": None,
        }
    consecutive_beats = 0
    for _, est, act, _ in past:
        if est is not None and act is not None and float(act) > float(est):
            consecutive_beats += 1
        else:
            break

    # Surprise trend: avg of last 3 surprise_pct
    surprises = [float(r[3]) for r in past[:3] if r[3] is not None]
    surprise_trend = round(sum(surprises) / len(surprises), 4) if surprises else None

    # B2 Sprint 9.5b: SUE (Standardized Unexpected Earnings)
    # SUE = (EPS_actual - EPS_estimate) / stdev(historical surprises)
    sue_last = None
    if past[0][1] is not None and past[0][2] is not None:
        raw_surprise = float(past[0][2]) - float(past[0][1])
        hist = [
            float(r[2]) - float(r[1])
            for r in past
            if r[1] is not None and r[2] is not None
        ]
        if len(hist) >= 2:
            mean_s = sum(hist) / len(hist)
            stdev_s = (sum((s - mean_s) ** 2 for s in hist) / (len(hist) - 1)) ** 0.5
            if stdev_s > 0.001:  # avoid division by near-zero
                sue_last = round(raw_surprise / stdev_s, 4)
        else:
            # Only 1 quarter: use raw surprise as-is (no normalization)
            sue_last = round(raw_surprise, 4)

    return {
        "consecutive_beats": consecutive_beats,
        "surprise_trend_3q": surprise_trend,
        "sue_last": sue_last,
        # B2: Days since last earnings (PEAD effect lasts 1-60 trading days)
        "days_since_last_earnings": (d - past[0][0]).days,
    }


def macro_derived(values: dict[str, float | None]) -> dict:
    """Derived macro features from raw FRED values."""
    dgs2, dgs10 = values.get("DGS2"), values.get("DGS10")
    vix = values.get("VIXCLS")
    hy = values.get("BAMLH0A0HYM2")
    dollar = values.get("DTWEXBGS")
    infl = values.get("T10YIE")
    # Derived: yield spread (10Y - 2Y), negative = inverted curve
    yield_spread = (
        round(dgs10 - dgs2, 4) if dgs10 is not None and dgs2 is not None else None
    )
    vix_regime = None
    if vix is not None:
        vix_regime = 0 if vix < 15 else 1 if vix < 25 else 2
    return {
        "macro_yield_spread": yield_spread,
        "macro_vix": round(vix, 2) if vix is not None else None,
        "macro_vix_regime": vix_regime,
        "macro_hy_spread": round(hy, 4) if hy is not None else None,
        "macro_dollar_index": round(dollar, 2) if dollar is not None else None,
        "macro_inflation_expectation": round(infl, 4) if infl is not None else None,
    }


def build_upsert_values(ticker: str, d: date, features: dict) -> dict:
    """Full row for the UPSERT: every feature column (missing → NULL)."""
    values = {"snapshot_date": d, "ticker": ticker}
    for col in FEATURE_COLUMNS:
        values[col] = features.get(col)
    values["feature_version"] = FEATURE_VERSION
    return values


class FeaturePipeline:
    """Compute daily feature snapshots for all point-in-time universe tickers."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._reset_caches()

    def _reset_caches(self) -> None:
        """Instance-level per-run caches (M4: no class-level mutable state)."""
        self._macro_cache: dict = {}
        self._sector_etf_cache: dict = {}  # (etf_ticker, date) -> metrics
        self._breadth_cache: dict = {}
        self._day_cache: dict = {}
        self._universe: list[str] | None = None

    def compute_daily(self, target_date: date) -> int:
        """Compute feature snapshots for all universe tickers on target_date.

        Returns 0 without writing anything if ``target_date`` is not an
        NYSE session (H3).

        Returns:
            Number of feature snapshot rows written/updated.
        """
        self._reset_caches()
        if not is_trading_day(target_date):
            logger.info(
                f"[feature_pipeline] {target_date} is not an NYSE session – skipped"
            )
            return 0

        tickers = self._get_active_tickers(target_date)
        self._universe = tickers
        logger.info(
            f"[feature_pipeline] Computing features for {len(tickers)} "
            f"tickers on {target_date} (version {FEATURE_VERSION})"
        )

        written = 0
        for ticker in tickers:
            features = self._compute_ticker(ticker, target_date)
            if self._upsert(ticker, target_date, features):
                written += 1

        self.session.flush()
        logger.info(
            f"[feature_pipeline] {target_date}: {written}/{len(tickers)} "
            f"snapshots written"
        )
        return written

    def _get_active_tickers(self, target_date: date) -> list[str]:
        """Return tickers that were active on target_date.

        Uses point-in-time index membership data if available,
        otherwise falls back to Universe.is_active == True.
        """
        from trading_signals.universe.manager import UniverseManager

        manager = UniverseManager(self.session)
        return manager.get_universe_as_of(target_date)

    def _sessions_back(self, d: date, n: int) -> list[date]:
        """``[d, d-1 session, …]`` – up to ``n`` sessions before d (cached)."""
        key = ("sessions", d, n)
        if key not in self._day_cache:
            days = trading_days(d - timedelta(days=int(n * 1.6) + 10), d)
            if not days or days[-1] != d:
                days = [x for x in days if x < d] + [d]
            self._day_cache[key] = list(reversed(days[-(n + 1) :]))
        return self._day_cache[key]

    def _compute_ticker(self, ticker: str, d: date) -> dict:
        """Compute all features for one ticker, gracefully degrading."""
        features: dict = {}
        for name, method in [
            ("ark", self._ark_features),
            ("insider", self._insider_features),
            ("analyst", self._analyst_features),
            ("politician", self._politician_features),
            ("13f", self._13f_features),
            ("fundamentals", self._fundamentals_features),
            ("technical", self._technical_features),
            ("liquidity", self._liquidity_features),
            ("earnings", self._earnings_features),
            ("sentiment", self._sentiment_features),
            ("macro", self._macro_features),
            ("breadth", self._breadth_features),
            ("sector", self._sector_features),
            ("short_interest", self._short_interest_features),
            ("options_iv", self._options_iv_features),
            ("estimates", self._estimates_features),
        ]:
            try:
                features.update(method(ticker, d))
            except Exception as e:
                logger.warning(f"[feature_pipeline] {ticker} {name} failed: {e}")
        return features

    # ── ARK Features (11) ────────────────────────────────────────────

    def _ark_day_states(self, d: date) -> tuple[list[dict], list[date]]:
        """Point-in-time ARK states for d and the previous 20 sessions."""
        key = ("ark", d)
        if key not in self._day_cache:
            sessions = self._sessions_back(d, ARK_LOOKBACK_SESSIONS)
            start = sessions[-1] - timedelta(days=ARK_MAX_AGE_DAYS)
            rows = [
                tuple(r)
                for r in self.session.execute(
                    select(
                        ARKHolding.snapshot_date,
                        ARKHolding.etf_ticker,
                        ARKHolding.ticker,
                        ARKHolding.weight_pct,
                        ARKHolding.shares,
                    ).where(ARKHolding.snapshot_date.between(start, d))
                ).all()
            ]
            states = [ark_state_at(rows, s) for s in sessions]
            self._day_cache[key] = (states, sessions)
        return self._day_cache[key]

    def _ark_features(self, ticker: str, d: date) -> dict:
        states, sessions = self._ark_day_states(d)
        return compute_ark_features(ticker, states, sessions)

    # ── Insider Features (10) ────────────────────────────────────────

    def _insider_features(self, ticker: str, d: date) -> dict:
        """Insider features as of d using only Form 4s public by d's close (C2).

        Public = EDGAR ``acceptance_datetime`` before 16:00 ET on d when
        known (migration 029), else ``filing_date <= d``, else
        ``transaction_date + FALLBACK_FILING_LAG_DAYS <= d``.
        """
        fallback_cut = d - timedelta(days=FALLBACK_FILING_LAG_DAYS)
        close_d = datetime.combine(d, MARKET_CLOSE_ET, tzinfo=NY_TZ)
        public_by_d = or_(
            InsiderTrade.acceptance_datetime < close_d,
            and_(
                InsiderTrade.acceptance_datetime.is_(None),
                InsiderTrade.filing_date <= d,
            ),
            and_(
                InsiderTrade.acceptance_datetime.is_(None),
                InsiderTrade.filing_date.is_(None),
                InsiderTrade.transaction_date <= fallback_cut,
            ),
        )

        # Buys/sells filed in the last 90 days
        trades = [
            tuple(r)
            for r in self.session.execute(
                select(
                    InsiderTrade.transaction_type,
                    InsiderTrade.filing_date,
                    InsiderTrade.total_value,
                )
                .where(InsiderTrade.ticker == ticker)
                .where(InsiderTrade.transaction_type.in_(("P", "S")))
                .where(InsiderTrade.is_derivative.is_(False))
                .where(InsiderTrade.filing_date.between(d - timedelta(days=90), d))
                .where(public_by_d)
            ).all()
        ]

        # All purchases public by d → point-in-time cluster rebuild
        purchases = list(
            self.session.execute(
                select(InsiderTrade)
                .where(InsiderTrade.ticker == ticker)
                .where(InsiderTrade.transaction_type == "P")
                .where(InsiderTrade.is_derivative.is_(False))
                .where(InsiderTrade.transaction_date >= data_start_date())
                .where(public_by_d)
                .order_by(InsiderTrade.transaction_date)
            )
            .scalars()
            .all()
        )

        covered = bool(trades) or bool(purchases)
        if not covered:
            covered = (
                self.session.execute(
                    select(InsiderTrade.id)
                    .where(InsiderTrade.ticker == ticker)
                    .where(public_by_d)
                    .limit(1)
                ).first()
                is not None
            )
        if not covered:
            return {}

        return {
            **insider_trade_features(trades, d),
            **insider_cluster_features(purchases, d),
        }

    # ── Analyst Features (7) ─────────────────────────────────────────

    def _analyst_features(self, ticker: str, d: date) -> dict:
        since_30 = d - timedelta(days=30)
        since_60 = d - timedelta(days=60)

        recent = [
            tuple(r)
            for r in self.session.execute(
                select(AnalystRating.action, AnalystRating.rating_date)
                .where(AnalystRating.ticker == ticker)
                .where(AnalystRating.rating_date <= d)
                .order_by(AnalystRating.rating_date.desc())
                .limit(10)
            ).all()
        ]
        result: dict = {"analyst_price_target_upside": self._target_upside(ticker, d)}
        if not recent:
            return result  # no coverage → counts stay NULL

        rating_score = _RATING_SCORES.get(recent[0][0]) if recent[0][0] else None

        counts = self.session.execute(
            select(
                func.sum(
                    case(
                        (
                            and_(
                                AnalystRating.action == "up",
                                AnalystRating.rating_date >= since_30,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (
                            and_(
                                AnalystRating.action == "down",
                                AnalystRating.rating_date >= since_30,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(case((AnalystRating.action == "up", 1), else_=0)),
                func.sum(case((AnalystRating.action == "down", 1), else_=0)),
            )
            .where(AnalystRating.ticker == ticker)
            .where(AnalystRating.rating_date.between(since_60, d))
        ).first()
        up30, down30, up60, down60 = (int(x or 0) for x in (counts or (0, 0, 0, 0)))

        # Upgrade streak
        streak = 0
        for action, _ in recent:
            if action == "up":
                streak += 1
            else:
                break

        result.update(
            {
                "analyst_rating_score": rating_score,
                "analyst_upgrades_30d": up30,
                "analyst_downgrades_30d": down30,
                "analyst_net_sentiment_30d": up30 - down30,
                "analyst_net_sentiment_60d": up60 - down60,
                "analyst_upgrade_streak": streak,
            }
        )
        return result

    def _latest_close(self, ticker: str, d: date) -> float | None:
        """Latest real close on or before d (max age bounded)."""
        val = self.session.execute(
            select(PriceDaily.close)
            .where(PriceDaily.ticker == ticker)
            .where(PriceDaily.trade_date <= d)
            .where(PriceDaily.trade_date >= d - timedelta(days=MAX_SNAPSHOT_AGE_DAYS))
            .where(_not_extrapolated())
            .order_by(PriceDaily.trade_date.desc())
            .limit(1)
        ).scalar()
        return _f(val)

    def _target_upside(self, ticker: str, d: date) -> float | None:
        """Price target upside from the latest (≤ 14 days old) fundamentals."""
        target = self.session.execute(
            select(FundamentalsSnapshot.target_price_mean)
            .where(FundamentalsSnapshot.ticker == ticker)
            .where(FundamentalsSnapshot.snapshot_date <= d)
            .where(
                FundamentalsSnapshot.snapshot_date
                >= d - timedelta(days=MAX_SNAPSHOT_AGE_DAYS)
            )
            .order_by(FundamentalsSnapshot.snapshot_date.desc())
            .limit(1)
        ).scalar()
        if target is None:
            return None
        price = self._latest_close(ticker, d)
        if price is None or price <= 0:
            return None
        return round(float(target) / price - 1, 4)

    # ── Politician Features (4) ──────────────────────────────────────

    def _politician_features(self, ticker: str, d: date) -> dict:
        """Politician trade features with STOCK Act lag filter.

        C1 Sprint 9.5c: Only count trades where disclosure_lag <= 45 days.
        Trades with extreme delays (e.g. 800+ days) are noise, not signal.

        C3 (2026-10): every query – including the ``_transaction`` variants –
        requires ``disclosure_date <= d`` (no trades that were still secret).
        """
        since_60 = d - timedelta(days=60)
        since_90 = d - timedelta(days=90)
        max_lag_days = 45  # STOCK Act maximum reporting deadline

        def _pit_filter():
            """Public by d + disclosure_date - transaction_date <= 45 days."""
            return and_(
                PoliticianTrade.ticker == ticker,
                PoliticianTrade.transaction_date.isnot(None),
                PoliticianTrade.disclosure_date.isnot(None),
                PoliticianTrade.disclosure_date <= d,
                PoliticianTrade.transaction_date
                >= (PoliticianTrade.disclosure_date - timedelta(days=max_lag_days)),
            )

        def _count(date_col, since, txn_type="Purchase"):
            return (
                self.session.execute(
                    select(func.count())
                    .where(_pit_filter())
                    .where(PoliticianTrade.transaction_type == txn_type)
                    .where(date_col.between(since, d))
                ).scalar()
                or 0
            )

        def _distinct(date_col, since):
            return (
                self.session.execute(
                    select(func.count(func.distinct(PoliticianTrade.politician_name)))
                    .where(_pit_filter())
                    .where(date_col.between(since, d))
                ).scalar()
                or 0
            )

        bc_disc = _count(PoliticianTrade.disclosure_date, since_60)
        dp_disc = _distinct(PoliticianTrade.disclosure_date, since_90)
        bc_txn = _count(PoliticianTrade.transaction_date, since_60)
        dp_txn = _distinct(PoliticianTrade.transaction_date, since_90)

        covered = bool(bc_disc or dp_disc or bc_txn or dp_txn)
        if not covered:
            covered = (
                self.session.execute(
                    select(PoliticianTrade.id).where(_pit_filter()).limit(1)
                ).first()
                is not None
            )
        if not covered:
            return {}
        return {
            "politician_buy_count_60d_disclosure": int(bc_disc),
            "politician_distinct_90d_disclosure": int(dp_disc),
            "politician_buy_count_60d_transaction": int(bc_txn),
            "politician_distinct_90d_transaction": int(dp_txn),
        }

    # ── 13F Features (4) ─────────────────────────────────────────────

    def _13f_features(self, ticker: str, d: date) -> dict:
        # Latest report period whose filing was public by day d
        # NOTE: filing_date is when the 13F was submitted to EDGAR,
        # which can be up to 45 days after report_period (quarter end).
        # Only plain share positions count as holders (PUT/CALL rows are
        # stored separately since migration 029).
        shares_only = Form13FHolding.put_call == "SH"
        latest_period = self.session.execute(
            select(func.max(Form13FHolding.report_period))
            .where(Form13FHolding.ticker == ticker)
            .where(Form13FHolding.filing_date <= d)  # point-in-time: publicly filed
            .where(shares_only)
        ).scalar()
        if not latest_period:
            return {}
        if latest_period < d - timedelta(days=FORM13F_MAX_AGE_DAYS):
            return {}  # M3: stale holdings are not forward-filled forever

        def _filers(period) -> set:
            return set(
                r[0]
                for r in self.session.execute(
                    select(Form13FHolding.filer_cik)
                    .where(Form13FHolding.ticker == ticker)
                    .where(Form13FHolding.report_period == period)
                    .where(Form13FHolding.filing_date <= d)
                    .where(shares_only)
                ).all()
            )

        current_filers = _filers(latest_period)

        # Previous quarter for new positions comparison
        prev_period = self.session.execute(
            select(func.max(Form13FHolding.report_period))
            .where(Form13FHolding.ticker == ticker)
            .where(Form13FHolding.report_period < latest_period)
            .where(Form13FHolding.filing_date <= d)
            .where(shares_only)
        ).scalar()

        new_positions = None
        exited_positions = None
        holder_delta_qoq = None
        if prev_period:
            prev_filers = _filers(prev_period)
            new_positions = len(current_filers - prev_filers)
            exited_positions = len(prev_filers - current_filers)

            # E1 Sprint 9.5b: Net holder change QoQ
            prev_count = len(prev_filers)
            if prev_count > 0:
                holder_delta_qoq = round(
                    (len(current_filers) - prev_count) / prev_count, 4
                )

        return {
            "form13f_top_holder_count": len(current_filers),
            "form13f_new_positions_count": new_positions,
            "form13f_exited_positions_count": exited_positions,
            "form13f_holder_delta_qoq": holder_delta_qoq,
        }

    # ── Fundamentals Features (8) ────────────────────────────────────

    def _fundamentals_features(self, ticker: str, d: date) -> dict:
        latest = self.session.execute(
            select(FundamentalsSnapshot)
            .where(FundamentalsSnapshot.ticker == ticker)
            .where(FundamentalsSnapshot.snapshot_date <= d)
            .where(
                FundamentalsSnapshot.snapshot_date
                >= d - timedelta(days=MAX_SNAPSHOT_AGE_DAYS)
            )
            .order_by(FundamentalsSnapshot.snapshot_date.desc())
            .limit(1)
        ).scalar()
        if not latest:
            return {}

        result = {
            "pe_ratio": _f(latest.pe_ratio),
            "forward_pe": _f(latest.forward_pe),
            "ps_ratio": _f(latest.ps_ratio),
            "revenue_growth_yoy": _f(latest.revenue_growth_yoy),
            "profit_margin": _f(latest.profit_margin),
            "debt_to_equity": _f(latest.debt_to_equity),
        }

        # Temporal: 4-week trends (linear regression slope)
        since_4w = d - timedelta(weeks=4)
        snapshots = list(
            self.session.execute(
                select(
                    FundamentalsSnapshot.snapshot_date,
                    FundamentalsSnapshot.pe_ratio,
                    FundamentalsSnapshot.profit_margin,
                )
                .where(FundamentalsSnapshot.ticker == ticker)
                .where(FundamentalsSnapshot.snapshot_date.between(since_4w, d))
                .order_by(FundamentalsSnapshot.snapshot_date)
            ).all()
        )

        if len(snapshots) >= 2:
            result["pe_trend_4w"] = _slope([s[1] for s in snapshots])
            result["margin_trend_4w"] = _slope([s[2] for s in snapshots])
        else:
            result["pe_trend_4w"] = None
            result["margin_trend_4w"] = None

        return result

    # ── Technical Features (6) ───────────────────────────────────────

    def _technical_features(self, ticker: str, d: date) -> dict:
        """TA features from the latest real session ≤ d (max 14 days old).

        Indicator row and price row are taken from the SAME session, so
        ratios never mix dates; extrapolated price rows are skipped.
        """
        row = self.session.execute(
            select(TechnicalIndicator, PriceDaily.close, PriceDaily.volume)
            .join(
                PriceDaily,
                and_(
                    PriceDaily.ticker == TechnicalIndicator.ticker,
                    PriceDaily.trade_date == TechnicalIndicator.trade_date,
                ),
            )
            .where(TechnicalIndicator.ticker == ticker)
            .where(TechnicalIndicator.trade_date <= d)
            .where(
                TechnicalIndicator.trade_date
                >= d - timedelta(days=MAX_SNAPSHOT_AGE_DAYS)
            )
            .where(_not_extrapolated())
            .order_by(TechnicalIndicator.trade_date.desc())
            .limit(1)
        ).first()
        if not row:
            return {}
        ti, close, vol = row[0], _f(row[1]), row[2]
        if close is None or close <= 0:
            return {}
        vol = int(vol) if vol is not None else None

        def _rel(sma_val):
            if sma_val is not None and float(sma_val) > 0:
                return round(close / float(sma_val) - 1, 4)
            return None

        vol_ratio = None
        if (
            vol is not None
            and ti.volume_sma_20 is not None
            and float(ti.volume_sma_20) > 0
        ):
            vol_ratio = round(vol / float(ti.volume_sma_20), 4)

        atr_pct = None
        if ti.atr_14 is not None:
            atr_pct = round(float(ti.atr_14) / close, 4)

        return {
            "price_vs_sma50": _rel(ti.sma_50),
            "price_vs_sma200": _rel(ti.sma_200),
            "rsi_14": _f(ti.rsi_14),
            "relative_strength_spy": _f(ti.relative_strength_spy),
            "volume_ratio_20d": vol_ratio,
            "atr_14_pct": atr_pct,
        }

    # ── Liquidity Features (2, Sprint 9.5b E4) ───────────────────────

    def _liquidity_features(self, ticker: str, d: date) -> dict:
        """Dollar volume and Amihud illiquidity ratio over 20 trading days.

        dollar_volume_20d: 20-day mean of Close * Volume (in USD)
        amihud_illiquidity_20d: mean(|daily_return| / dollar_volume)
        Higher Amihud = less liquid = higher price impact per dollar traded.
        Uses the last 21 real (non-extrapolated) sessions ≤ d.
        """
        rows = list(
            self.session.execute(
                select(PriceDaily.close, PriceDaily.volume)
                .where(PriceDaily.ticker == ticker)
                .where(PriceDaily.trade_date <= d)
                .where(PriceDaily.trade_date >= d - timedelta(days=45))
                .where(_not_extrapolated())
                .order_by(PriceDaily.trade_date.desc())
                .limit(21)
            ).all()
        )
        rows = [tuple(r) for r in reversed(rows)]
        return liquidity_from_prices(rows)

    # ── Earnings Features (5, Sprint 9.5b) ───────────────────────────

    def _earnings_features(self, ticker: str, d: date) -> dict:
        # Days until next earnings.
        # M2: if the collector records when a date was first seen, only
        # dates known by d are used. Rows whose first_seen lies AFTER the
        # earnings date were collected retroactively (migration 029
        # back-fills first_seen from fetched_at), so first_seen says nothing
        # about the announcement date – they keep the documented lookahead
        # (companies announce ~3 weeks ahead), as do rows without
        # first_seen. The forward window is capped at 90 days.
        max_forward = d + timedelta(days=90)
        stmt = (
            select(func.min(EarningsCalendar.earnings_date))
            .where(EarningsCalendar.ticker == ticker)
            .where(EarningsCalendar.earnings_date >= d)
            .where(EarningsCalendar.earnings_date <= max_forward)
        )
        first_seen = EarningsCalendar.__table__.columns.get("first_seen")
        if first_seen is not None:
            seen_day = func.date(first_seen)
            stmt = stmt.where(
                or_(
                    first_seen.is_(None),
                    seen_day <= d,
                    seen_day > EarningsCalendar.earnings_date,
                )
            )
        next_earn = self.session.execute(stmt).scalar()
        days_until = (next_earn - d).days if next_earn else None

        # Past earnings for beat streak + surprise trend
        past = [
            tuple(r)
            for r in self.session.execute(
                select(
                    EarningsCalendar.earnings_date,
                    EarningsCalendar.eps_estimate,
                    EarningsCalendar.eps_actual,
                    EarningsCalendar.surprise_pct,
                )
                .where(EarningsCalendar.ticker == ticker)
                .where(EarningsCalendar.earnings_date < d)
                .where(EarningsCalendar.eps_actual.isnot(None))
                .order_by(EarningsCalendar.earnings_date.desc())
                .limit(4)
            ).all()
        ]
        return {
            "earnings_days_until": days_until,
            **earnings_history_features(past, d),
        }

    # ── Sentiment Features (7, Sprint 8c / 9.5b E3) ──────────────────

    def _sentiment_window(
        self, ticker: str | None, start: datetime, end: datetime
    ) -> tuple[float | None, int, int]:
        """(avg_score, neg_count, total_count) for published_at in [start, end)."""
        from trading_signals.db.models.news import NewsArticle

        stmt = (
            select(
                func.avg(NewsSentiment.sentiment_score),
                func.count(),
                func.sum(
                    case((NewsSentiment.sentiment_label == "negative", 1), else_=0)
                ),
            )
            .select_from(NewsSentiment)
            .join(NewsArticle, NewsSentiment.article_id == NewsArticle.id)
            .where(NewsSentiment.model_version == SENTIMENT_MODEL_VERSION)
            .where(NewsArticle.published_at >= start)
            .where(NewsArticle.published_at < end)
        )
        if ticker is not None:
            stmt = stmt.where(NewsSentiment.ticker == ticker)
        else:
            stmt = stmt.where(NewsSentiment.ticker.is_(None))
        row = self.session.execute(stmt).first()
        if not row:
            return None, 0, 0
        return _f(row[0]), int(row[2] or 0), int(row[1] or 0)

    def _sentiment_features(self, ticker: str, d: date) -> dict:
        """Sentiment features from news articles.

        Windows end at 16:00 America/New_York on d (C4) and filter on
        ``published_at`` (not scoring date) and a single model version.
        Also includes a global market sentiment feature as contextual signal.
        """
        cutoff = news_cutoff_utc(d)
        since_7 = cutoff - timedelta(days=7)
        since_30 = cutoff - timedelta(days=30)

        # Ticker-specific sentiment
        avg_7d, neg_7d, count_7d = self._sentiment_window(ticker, since_7, cutoff)
        avg_30d, _, _ = self._sentiment_window(ticker, since_30, cutoff)

        # Momentum: short-term vs long-term sentiment
        momentum = None
        if avg_7d is not None and avg_30d is not None:
            momentum = round(avg_7d - avg_30d, 4)

        # Global market sentiment (articles without ticker association)
        key = ("market_sentiment", d)
        if key not in self._day_cache:
            self._day_cache[key] = self._sentiment_window(None, since_7, cutoff)[0]
        market_7d = self._day_cache[key]

        has_data = avg_7d is not None or avg_30d is not None or count_7d > 0

        # E3 Sprint 9.5b: News volume ratio (spike detection)
        # 7d article count vs 90d average → unusual news activity
        news_vol_ratio = None
        if has_data:
            _, _, count_90d = self._sentiment_window(
                ticker, cutoff - timedelta(days=90), cutoff
            )
            if count_90d > 0:
                avg_weekly_90d = count_90d / (90 / 7)
                news_vol_ratio = round(count_7d / avg_weekly_90d, 4)

        return {
            "sentiment_avg_7d": round(avg_7d, 4) if avg_7d is not None else None,
            "sentiment_avg_30d": round(avg_30d, 4) if avg_30d is not None else None,
            "sentiment_momentum": momentum,
            "sentiment_neg_count_7d": neg_7d if has_data else None,
            "sentiment_article_count_7d": count_7d if has_data else None,
            "market_sentiment_7d": (
                round(market_7d, 4) if market_7d is not None else None
            ),
            "news_volume_ratio_7d": news_vol_ratio,
        }

    # ── Macro Features (6, Sprint 9.5b) ──────────────────────────────

    def _macro_features(self, ticker: str, d: date) -> dict:
        """Market-wide macro features from FRED data (same for all tickers).

        H8: daily series use observations up to the previous NYSE session
        (FRED publishes the value of d on d+1); DTWEXBGS uses a 7-day lag.
        Cached per instance and date.
        """
        if d in self._macro_cache:
            return self._macro_cache[d]

        prev_session = previous_trading_day(d)

        def _latest_value(series_id: str, as_of: date, max_age: int) -> float | None:
            val = self.session.execute(
                select(MacroSeries.value)
                .where(MacroSeries.series_id == series_id)
                .where(MacroSeries.obs_date <= as_of)
                .where(MacroSeries.obs_date >= as_of - timedelta(days=max_age))
                .where(MacroSeries.value.isnot(None))
                .order_by(MacroSeries.obs_date.desc())
                .limit(1)
            ).scalar()
            return _f(val)

        values: dict[str, float | None] = {}
        for sid in DAILY_MACRO_SERIES:
            values[sid] = _latest_value(sid, prev_session, MACRO_MAX_AGE_DAYS)
        for sid, lag in WEEKLY_MACRO_LAG_DAYS.items():
            values[sid] = _latest_value(
                sid, d - timedelta(days=lag), MACRO_MAX_AGE_DAYS + 7
            )

        result = macro_derived(values)
        self._macro_cache[d] = result
        return result

    # ── Sector-Relative Features (B4, Sprint 9.5b) ───────────────────

    def _sector_of(self, ticker: str) -> str | None:
        """Current Universe.sector (NOT point-in-time – documented)."""
        key = ("sector_map",)
        if key not in self._day_cache:
            self._day_cache[key] = {
                t: s
                for t, s in self.session.execute(
                    select(Universe.ticker, Universe.sector)
                ).all()
            }
        return self._day_cache[key].get(ticker)

    def _close_on_or_before(self, ticker: str, d: date) -> float | None:
        val = self.session.execute(
            select(PriceDaily.close)
            .where(PriceDaily.ticker == ticker)
            .where(PriceDaily.trade_date <= d)
            .where(PriceDaily.trade_date >= d - timedelta(days=MAX_SNAPSHOT_AGE_DAYS))
            .where(_not_extrapolated())
            .order_by(PriceDaily.trade_date.desc())
            .limit(1)
        ).scalar()
        return _f(val)

    def _sma50_on_or_before(self, ticker: str, d: date) -> float | None:
        val = self.session.execute(
            select(TechnicalIndicator.sma_50)
            .where(TechnicalIndicator.ticker == ticker)
            .where(TechnicalIndicator.trade_date <= d)
            .where(
                TechnicalIndicator.trade_date
                >= d - timedelta(days=MAX_SNAPSHOT_AGE_DAYS)
            )
            .order_by(TechnicalIndicator.trade_date.desc())
            .limit(1)
        ).scalar()
        return _f(val)

    def _sector_features(self, ticker: str, d: date) -> dict:
        """Sector-relative features for stock-vs-sector neutralization.

        - sector_relative_return_20d: stock 20d return minus sector ETF 20d return
        - sector_relative_momentum: stock price_vs_sma50 minus sector ETF's

        Returns empty dict for benchmark ETFs or stocks without sector mapping.
        """
        sector = self._sector_of(ticker)
        if not sector or sector == "Benchmark":
            return {}

        etf_ticker = _SECTOR_ETF_MAP.get(sector)
        if not etf_ticker:
            return {}

        # Get sector ETF data (cached per ETF+date)
        cache_key = (etf_ticker, d)
        if cache_key not in self._sector_etf_cache:
            self._sector_etf_cache[cache_key] = self._get_etf_metrics(etf_ticker, d)

        etf_data = self._sector_etf_cache[cache_key]
        if not etf_data:
            return {}

        result = {}

        # Sector-relative return: stock 20d return minus ETF 20d return
        stock_price = self._close_on_or_before(ticker, d)
        stock_price_20d = self._close_on_or_before(ticker, d - timedelta(days=28))

        if stock_price is not None and stock_price_20d and stock_price_20d > 0:
            stock_ret = stock_price / stock_price_20d - 1
            if etf_data.get("return_20d") is not None:
                result["sector_relative_return_20d"] = round(
                    stock_ret - etf_data["return_20d"], 6
                )

        # Sector-relative momentum: stock price_vs_sma50 minus ETF's
        sma = self._sma50_on_or_before(ticker, d)
        if stock_price is not None and sma and sma > 0:
            stock_vs_sma50 = stock_price / sma - 1
            if etf_data.get("pct_vs_sma50") is not None:
                result["sector_relative_momentum"] = round(
                    stock_vs_sma50 - etf_data["pct_vs_sma50"], 6
                )

        return result

    def _get_etf_metrics(self, etf_ticker: str, d: date) -> dict | None:
        """Compute return_20d and pct_vs_sma50 for a sector ETF."""
        price = self._close_on_or_before(etf_ticker, d)
        if not price:
            return None
        price_20d = self._close_on_or_before(etf_ticker, d - timedelta(days=28))
        sma50 = self._sma50_on_or_before(etf_ticker, d)

        result = {}
        if price_20d and price_20d > 0:
            result["return_20d"] = price / price_20d - 1
        if sma50 and sma50 > 0:
            result["pct_vs_sma50"] = price / sma50 - 1
        return result if result else None

    # ── Market Breadth Features (2, Sprint 9.5b D2) ──────────────────

    def _breadth_features(self, ticker: str, d: date) -> dict:
        """Market-wide breadth features computed from prices_daily.

        Same for every ticker on a given day, cached after first call.
        H6: computed on session d only, against the previous session, over
        the point-in-time universe of d (benchmark ETFs excluded) and real
        (non-extrapolated) prices.

        - breadth_advance_decline: advances / (advances + declines) on day d
        - breadth_pct_above_sma50: % of universe tickers with close > SMA50

        Uses a SAVEPOINT so that SQL errors don't poison the outer transaction.
        """
        if d in self._breadth_cache:
            return self._breadth_cache[d]

        universe = self._universe
        if universe is None:
            universe = self._get_active_tickers(d)
            self._universe = universe

        ad_ratio = None
        pct_above = None
        if universe:
            nested = self.session.begin_nested()
            try:
                prev_session = previous_trading_day(d)
                cur = aliased(PriceDaily)
                prev = aliased(PriceDaily)
                not_benchmark = or_(
                    Universe.sector.is_(None), Universe.sector != "Benchmark"
                )

                ad = self.session.execute(
                    select(
                        func.sum(case((cur.close > prev.close, 1), else_=0)),
                        func.sum(case((cur.close < prev.close, 1), else_=0)),
                    )
                    .select_from(cur)
                    .join(
                        prev,
                        and_(
                            prev.ticker == cur.ticker, prev.trade_date == prev_session
                        ),
                    )
                    .join(Universe, Universe.ticker == cur.ticker)
                    .where(cur.trade_date == d)
                    .where(cur.ticker.in_(universe))
                    .where(_not_extrapolated(cur.is_extrapolated))
                    .where(_not_extrapolated(prev.is_extrapolated))
                    .where(not_benchmark)
                ).first()
                if ad and ad[0] is not None:
                    advances, declines = int(ad[0]), int(ad[1] or 0)
                    if advances + declines > 0:
                        ad_ratio = round(advances / (advances + declines), 4)

                sma = self.session.execute(
                    select(
                        func.count().filter(
                            PriceDaily.close > TechnicalIndicator.sma_50
                        ),
                        func.count(),
                    )
                    .select_from(TechnicalIndicator)
                    .join(
                        PriceDaily,
                        and_(
                            PriceDaily.ticker == TechnicalIndicator.ticker,
                            PriceDaily.trade_date == TechnicalIndicator.trade_date,
                        ),
                    )
                    .join(Universe, Universe.ticker == TechnicalIndicator.ticker)
                    .where(TechnicalIndicator.trade_date == d)
                    .where(TechnicalIndicator.sma_50.isnot(None))
                    .where(TechnicalIndicator.ticker.in_(universe))
                    .where(_not_extrapolated())
                    .where(not_benchmark)
                ).first()
                if sma and sma[1]:
                    pct_above = round(int(sma[0]) / int(sma[1]), 4)
                nested.commit()
            except Exception as e:
                nested.rollback()
                logger.warning(f"[feature_pipeline] breadth query failed: {e}")

        result = {
            "breadth_advance_decline": ad_ratio,
            "breadth_pct_above_sma50": pct_above,
        }
        self._breadth_cache[d] = result
        return result

    # ── Short Volume Features (3, Sprint 9.5c B5) ────────────────────

    def _short_interest_features(self, ticker: str, d: date) -> dict:
        """Short interest features from daily short volume data.

        - short_volume_ratio_5d: 5-day average of short_volume / total_volume
        - short_volume_ratio_20d: 20-day average
        - short_volume_change_20d: change in 20d ratio vs previous 20d period (momentum)
        """
        from trading_signals.db.models.short_interest import ShortVolume

        since_20 = d - timedelta(days=30)  # calendar days for ~20 trading days
        since_5 = d - timedelta(days=8)
        since_40 = d - timedelta(days=60)  # for previous 20d period

        # Get short volume ratios for last 60 calendar days
        rows = list(
            self.session.execute(
                select(ShortVolume.trade_date, ShortVolume.short_volume_ratio)
                .where(ShortVolume.ticker == ticker)
                .where(ShortVolume.trade_date.between(since_40, d))
                .where(ShortVolume.short_volume_ratio.isnot(None))
                .order_by(ShortVolume.trade_date)
            ).all()
        )

        if not rows:
            return {}

        # Split into recent 5d, recent 20d, and previous 20d
        ratios_5d = [float(r[1]) for r in rows if r[0] >= since_5]
        ratios_20d = [float(r[1]) for r in rows if r[0] >= since_20]
        ratios_prev_20d = [float(r[1]) for r in rows if r[0] < since_20]

        def _avg(vals):
            return round(sum(vals) / len(vals), 4) if vals else None

        avg_5d, avg_20d, avg_prev_20d = (
            _avg(ratios_5d),
            _avg(ratios_20d),
            _avg(ratios_prev_20d),
        )

        change = None
        if avg_20d is not None and avg_prev_20d is not None:
            change = round(avg_20d - avg_prev_20d, 4)

        return {
            "short_volume_ratio_5d": avg_5d,
            "short_volume_ratio_20d": avg_20d,
            "short_volume_change_20d": change,
        }

    def _options_iv_features(self, ticker: str, d: date) -> dict:
        """Options implied volatility features from daily IV snapshots.

        Returns the latest available IV snapshot on or before d (max age
        ``MAX_SNAPSHOT_AGE_DAYS``, M3).
        IV-Rank requires ~1 year of data and will be added later.
        """
        row = self.session.execute(
            select(
                OptionsIVSnapshot.atm_iv_30d,
                OptionsIVSnapshot.skew_25d,
                OptionsIVSnapshot.term_slope,
                OptionsIVSnapshot.put_call_oi,
            )
            .where(OptionsIVSnapshot.ticker == ticker)
            .where(OptionsIVSnapshot.snapshot_date <= d)
            .where(
                OptionsIVSnapshot.snapshot_date
                >= d - timedelta(days=MAX_SNAPSHOT_AGE_DAYS)
            )
            .order_by(OptionsIVSnapshot.snapshot_date.desc())
            .limit(1)
        ).first()

        if not row:
            return {}

        return {
            "options_iv_atm_30d": _f(row[0]),
            "options_iv_skew_25d": _f(row[1]),
            "options_iv_term_slope": _f(row[2]),
            "options_iv_put_call_oi": _f(row[3]),
        }

    def _estimates_features(self, ticker: str, d: date) -> dict:
        """Analyst estimates revision features.

        Measures the direction and magnitude of EPS/revenue consensus revisions.
        Revisions Momentum is one of the strongest short-term predictive signals.

        Uses current-quarter ('0q') estimates as the primary signal
        (latest snapshot ≤ d, max age ``MAX_SNAPSHOT_AGE_DAYS``).
        """
        row = self.session.execute(
            select(EstimatesSnapshot)
            .where(EstimatesSnapshot.ticker == ticker)
            .where(EstimatesSnapshot.period == "0q")
            .where(EstimatesSnapshot.as_of <= d)
            .where(EstimatesSnapshot.as_of >= d - timedelta(days=MAX_SNAPSHOT_AGE_DAYS))
            .order_by(EstimatesSnapshot.as_of.desc())
            .limit(1)
        ).scalar()

        if not row:
            return {}

        result = {}

        # EPS revision %: (current - N_days_ago) / abs(N_days_ago)
        if row.eps_current is not None and row.eps_30d_ago is not None:
            base = abs(float(row.eps_30d_ago))
            if base > 0.001:  # avoid division by near-zero
                result["eps_revision_pct_30d"] = round(
                    (float(row.eps_current) - float(row.eps_30d_ago)) / base, 6
                )

        if row.eps_current is not None and row.eps_90d_ago is not None:
            base = abs(float(row.eps_90d_ago))
            if base > 0.001:
                result["eps_revision_pct_90d"] = round(
                    (float(row.eps_current) - float(row.eps_90d_ago)) / base, 6
                )

        # Revenue revision %: compare latest revenue_avg with a 30d-ago snapshot
        if row.revenue_avg is not None:
            older = self.session.execute(
                select(EstimatesSnapshot.revenue_avg)
                .where(EstimatesSnapshot.ticker == ticker)
                .where(EstimatesSnapshot.period == "0q")
                .where(EstimatesSnapshot.as_of <= d - timedelta(days=30))
                .where(
                    EstimatesSnapshot.as_of
                    >= d - timedelta(days=30 + MAX_SNAPSHOT_AGE_DAYS)
                )
                .order_by(EstimatesSnapshot.as_of.desc())
                .limit(1)
            ).scalar()
            if older is not None:
                base = abs(float(older))
                if base > 1.0:  # revenue in absolute $, threshold higher
                    result["revenue_revision_pct_30d"] = round(
                        (float(row.revenue_avg) - float(older)) / base, 6
                    )

        # Net revision counts (up - down)
        if row.rev_up_7d is not None and row.rev_down_7d is not None:
            result["eps_revisions_net_7d"] = row.rev_up_7d - row.rev_down_7d
        if row.rev_up_30d is not None and row.rev_down_30d is not None:
            result["eps_revisions_net_30d"] = row.rev_up_30d - row.rev_down_30d

        return result

    # ── UPSERT ───────────────────────────────────────────────────────

    def _upsert(self, ticker: str, d: date, features: dict) -> bool:
        """Insert or fully overwrite a feature snapshot row (H1).

        All feature columns are written from the new computation – including
        NULLs – so stale values cannot survive a recompute; ``computed_at``
        and ``feature_version`` are refreshed. If no feature has a value,
        an existing row for (d, ticker) is deleted instead.

        Returns True if a row was written.
        """
        if all(features.get(c) is None for c in FEATURE_COLUMNS):
            self.session.execute(
                delete(FeatureSnapshot)
                .where(FeatureSnapshot.snapshot_date == d)
                .where(FeatureSnapshot.ticker == ticker)
            )
            return False

        values = build_upsert_values(ticker, d, features)
        stmt = pg_insert(FeatureSnapshot).values(**values)
        set_ = {col: stmt.excluded[col] for col in FEATURE_COLUMNS}
        set_["feature_version"] = stmt.excluded.feature_version
        set_["computed_at"] = func.now()
        stmt = stmt.on_conflict_do_update(
            index_elements=["snapshot_date", "ticker"],
            set_=set_,
        )
        self.session.execute(stmt)
        return True
