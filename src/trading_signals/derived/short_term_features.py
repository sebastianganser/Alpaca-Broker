"""Short-term price features (concept 2026-10-06 §7.1, step A2).

Nine features for the 1–14 session trading horizon, derived only from
``prices_daily`` (adjusted, extrapolated rows excluded) and past earnings
dates. All values describe the state at the **close of session d**; rolling
windows count real sessions (rows), not calendar days.

  st_return_1d            close(d) / close(d-1) - 1
  st_return_5d            close(d) / close(d-5) - 1
  st_gap                  open(d) / close(d-1) - 1
  st_close_location       (close - low) / (high - low) of d
  st_dist_52w_high        close(d) / max(high, last 252 sessions) - 1
  st_rsi_2                2-session RSI with simple means (no recursion)
  st_bollinger_pctb       position in the 20-session 2σ Bollinger band
  st_signed_volume_shock  ln(volume(d) / mean volume of the 20 prior
                          sessions) × sign(st_return_1d)
  st_earnings_reaction    close(first session after E) /
                          close(last session before E) - 1 for the latest
                          reported earnings date E, valid up to
                          ``EARNINGS_VALID_DAYS`` calendar days after E

One vectorised function (:func:`compute_short_term_frame`) serves both the
daily pipeline (last ``LOOKBACK_SESSIONS`` rows, value of the last row) and
the historical backfill (:class:`ShortTermBackfill`, full history), so both
paths produce the same numbers. No feature uses recursive smoothing, hence
the result does not depend on how much history is loaded.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from trading_signals.db.models.features import FeatureSnapshot
from trading_signals.db.models.fundamentals import EarningsCalendar
from trading_signals.db.models.prices import PriceDaily
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

SHORT_TERM_COLUMNS: tuple[str, ...] = (
    "st_return_1d",
    "st_return_5d",
    "st_gap",
    "st_close_location",
    "st_dist_52w_high",
    "st_rsi_2",
    "st_bollinger_pctb",
    "st_signed_volume_shock",
    "st_earnings_reaction",
)

#: Input columns expected by :func:`compute_short_term_frame`.
PRICE_COLUMNS: tuple[str, ...] = ("trade_date", "open", "high", "low", "close", "volume")

HIGH_WINDOW = 252
HIGH_MIN_PERIODS = 126
BB_WINDOW = 20
VOLUME_WINDOW = 20
RSI_WINDOW = 2
#: Calendar days after the earnings date during which the reaction is kept.
EARNINGS_VALID_DAYS = 60
#: Max calendar-day distance of the pre/post session from the earnings date
#: (protects against price gaps around the event).
EARNINGS_MAX_SESSION_GAP_DAYS = 5
#: Real sessions loaded for the daily computation (≥ HIGH_WINDOW + 1).
LOOKBACK_SESSIONS = 260
#: Calendar-day bound of the daily price query (≈ 260 sessions + holidays).
LOOKBACK_CALENDAR_DAYS = 400
DECIMALS = 6

#: Tickers per backfill chunk and rows per executemany batch.
TICKER_CHUNK = 50
UPDATE_BATCH = 5000


# ── Pure computation (unit-tested) ───────────────────────────────────


def _num(s: pd.Series) -> pd.Series:
    """Numeric float series; non-positive values → NaN."""
    out = pd.to_numeric(s, errors="coerce").astype(float)
    return out.where(out > 0)


def _earnings_reaction(
    dates: pd.Series, close: pd.Series, earnings_dates: Iterable[date]
) -> pd.Series:
    """Per-row reaction of the latest valid earnings event (see module doc)."""
    res = pd.Series(np.nan, index=dates.index, dtype=float)
    events = sorted({e for e in earnings_dates if e is not None})
    n = len(dates)
    if not events or n == 0:
        return res
    d_arr = pd.to_datetime(dates).to_numpy(dtype="datetime64[D]")
    c_arr = close.to_numpy(dtype=float)
    max_gap = np.timedelta64(EARNINGS_MAX_SESSION_GAP_DAYS, "D")
    valid = np.timedelta64(EARNINGS_VALID_DAYS, "D")
    for e in events:  # ascending → newer events overwrite older ones
        e64 = np.datetime64(e, "D")
        i_pre = int(np.searchsorted(d_arr, e64, side="left")) - 1
        i_post = int(np.searchsorted(d_arr, e64, side="right"))
        if i_pre < 0 or i_post >= n:
            continue
        if e64 - d_arr[i_pre] > max_gap or d_arr[i_post] - e64 > max_gap:
            continue
        pre, post = c_arr[i_pre], c_arr[i_post]
        if not (np.isfinite(pre) and np.isfinite(post)) or pre <= 0:
            continue
        j_end = int(np.searchsorted(d_arr, e64 + valid, side="right"))
        res.iloc[i_post:j_end] = post / pre - 1
    return res


def compute_short_term_frame(
    prices: pd.DataFrame, earnings_dates: Iterable[date] = ()
) -> pd.DataFrame:
    """Short-term features for every session of ONE ticker.

    Args:
        prices: Columns :data:`PRICE_COLUMNS`, real (non-extrapolated)
            sessions of a single ticker in any order.
        earnings_dates: Dates of reported earnings (``eps_actual`` known).

    Returns:
        ``trade_date`` + :data:`SHORT_TERM_COLUMNS`, one row per session,
        sorted by date, rounded to :data:`DECIMALS`; missing → NaN. A row
        only uses information up to its own date.
    """
    out_cols = ["trade_date", *SHORT_TERM_COLUMNS]
    if prices is None or prices.empty:
        return pd.DataFrame(columns=out_cols)
    p = (
        prices.sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )
    o, h, lo, c = _num(p["open"]), _num(p["high"]), _num(p["low"]), _num(p["close"])
    v = _num(p["volume"])
    prev_c = c.shift(1)

    out = pd.DataFrame({"trade_date": p["trade_date"]})
    ret1 = c / prev_c - 1
    out["st_return_1d"] = ret1
    out["st_return_5d"] = c / c.shift(5) - 1
    out["st_gap"] = o / prev_c - 1

    rng = h - lo
    out["st_close_location"] = ((c - lo) / rng).where(rng > 0).clip(0.0, 1.0)

    high_max = h.rolling(HIGH_WINDOW, min_periods=HIGH_MIN_PERIODS).max()
    out["st_dist_52w_high"] = c / high_max - 1

    diff = c.diff()
    gain = diff.clip(lower=0).rolling(RSI_WINDOW, min_periods=RSI_WINDOW).mean()
    loss = (-diff).clip(lower=0).rolling(RSI_WINDOW, min_periods=RSI_WINDOW).mean()
    total = gain + loss
    rsi = (100.0 * gain / total).where(total > 0, 50.0)
    out["st_rsi_2"] = rsi.where(gain.notna() & loss.notna())

    mean = c.rolling(BB_WINDOW, min_periods=BB_WINDOW).mean()
    std = c.rolling(BB_WINDOW, min_periods=BB_WINDOW).std(ddof=0)
    out["st_bollinger_pctb"] = ((c - (mean - 2 * std)) / (4 * std)).where(
        std > 1e-9 * mean
    )

    vol_prev = v.shift(1).rolling(VOLUME_WINDOW, min_periods=VOLUME_WINDOW).mean()
    out["st_signed_volume_shock"] = np.log(v / vol_prev) * np.sign(ret1)

    out["st_earnings_reaction"] = _earnings_reaction(p["trade_date"], c, earnings_dates)

    feats = list(SHORT_TERM_COLUMNS)
    # "+ 0.0" turns -0.0 (e.g. sign 0 × negative log) into 0.0
    out[feats] = out[feats].replace([np.inf, -np.inf], np.nan).round(DECIMALS) + 0.0
    return out[out_cols]


def short_term_features_at(
    prices: pd.DataFrame, earnings_dates: Iterable[date], d: date
) -> dict:
    """Features of session ``d`` (empty dict if ``d`` has no real price row).

    ``prices`` must only contain sessions ≤ d (the daily pipeline loads the
    last :data:`LOOKBACK_SESSIONS`).
    """
    frame = compute_short_term_frame(prices, earnings_dates)
    if frame.empty or frame["trade_date"].iloc[-1] != d:
        return {}
    last = frame.iloc[-1]
    return {c: (None if pd.isna(last[c]) else float(last[c])) for c in SHORT_TERM_COLUMNS}


# ── DB helpers ───────────────────────────────────────────────────────


def load_short_term_inputs(
    session: Session, ticker: str, d: date
) -> tuple[pd.DataFrame, list[date]]:
    """Price rows (last LOOKBACK_SESSIONS real sessions ≤ d) + earnings dates."""
    rows = session.execute(
        select(
            PriceDaily.trade_date,
            PriceDaily.open,
            PriceDaily.high,
            PriceDaily.low,
            PriceDaily.close,
            PriceDaily.volume,
        )
        .where(PriceDaily.ticker == ticker)
        .where(PriceDaily.trade_date <= d)
        .where(PriceDaily.trade_date >= d - timedelta(days=LOOKBACK_CALENDAR_DAYS))
        .where(PriceDaily.is_extrapolated.is_not(True))
        .order_by(PriceDaily.trade_date.desc())
        .limit(LOOKBACK_SESSIONS)
    ).all()
    prices = pd.DataFrame([tuple(r) for r in rows], columns=list(PRICE_COLUMNS))
    earnings = [
        r[0]
        for r in session.execute(
            select(EarningsCalendar.earnings_date)
            .where(EarningsCalendar.ticker == ticker)
            .where(EarningsCalendar.earnings_date < d)
            .where(
                EarningsCalendar.earnings_date
                >= d - timedelta(days=EARNINGS_VALID_DAYS + 10)
            )
            .where(EarningsCalendar.eps_actual.isnot(None))
        ).all()
    ]
    return prices, earnings


class ShortTermBackfill:
    """Recompute the ``st_*`` columns of existing feature snapshots.

    Only the short-term columns (and optionally ``feature_version``) are
    written; all other features stay untouched. Does not commit.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def all_tickers(self) -> list[str]:
        return [
            r[0]
            for r in self.session.execute(
                select(FeatureSnapshot.ticker).distinct().order_by(FeatureSnapshot.ticker)
            ).all()
        ]

    def recompute_tickers(
        self, tickers: Iterable[str], feature_version: str | None = None
    ) -> int:
        """Recompute all snapshots of ``tickers``; returns rows updated."""
        unique = sorted({t for t in tickers if t})
        total = 0
        for i in range(0, len(unique), TICKER_CHUNK):
            total += self._process_chunk(unique[i : i + TICKER_CHUNK], feature_version)
        self.session.flush()
        return total

    def _process_chunk(self, tickers: list[str], feature_version: str | None) -> int:
        snaps = pd.DataFrame(
            self.session.execute(
                select(FeatureSnapshot.ticker, FeatureSnapshot.snapshot_date).where(
                    FeatureSnapshot.ticker.in_(tickers)
                )
            ).all(),
            columns=["ticker", "snapshot_date"],
        )
        if snaps.empty:
            return 0
        start = snaps["snapshot_date"].min() - timedelta(days=LOOKBACK_CALENDAR_DAYS)
        end = snaps["snapshot_date"].max()
        prices = pd.DataFrame(
            [
                tuple(r)
                for r in self.session.execute(
                    select(
                        PriceDaily.ticker,
                        PriceDaily.trade_date,
                        PriceDaily.open,
                        PriceDaily.high,
                        PriceDaily.low,
                        PriceDaily.close,
                        PriceDaily.volume,
                    )
                    .where(PriceDaily.ticker.in_(tickers))
                    .where(PriceDaily.trade_date.between(start, end))
                    .where(PriceDaily.is_extrapolated.is_not(True))
                ).all()
            ],
            columns=["ticker", *PRICE_COLUMNS],
        )
        earnings: dict[str, list[date]] = {}
        for t, e in self.session.execute(
            select(EarningsCalendar.ticker, EarningsCalendar.earnings_date)
            .where(EarningsCalendar.ticker.in_(tickers))
            .where(EarningsCalendar.eps_actual.isnot(None))
        ).all():
            earnings.setdefault(t, []).append(e)

        frames = []
        for ticker, grp in prices.groupby("ticker", sort=False):
            f = compute_short_term_frame(grp[list(PRICE_COLUMNS)], earnings.get(ticker, []))
            f.insert(0, "ticker", ticker)
            frames.append(f)
        feats = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=["ticker", "trade_date", *SHORT_TERM_COLUMNS])
        )
        merged = snaps.merge(
            feats.rename(columns={"trade_date": "snapshot_date"}),
            on=["ticker", "snapshot_date"],
            how="left",
        )
        return self._bulk_update(merged, feature_version)

    def _bulk_update(self, merged: pd.DataFrame, feature_version: str | None) -> int:
        params = []
        for row in merged.to_dict("records"):
            p = {"snapshot_date": row["snapshot_date"], "ticker": row["ticker"]}
            for col in SHORT_TERM_COLUMNS:
                v = row.get(col)
                p[col] = None if v is None or pd.isna(v) else float(v)
            if feature_version is not None:
                p["feature_version"] = feature_version
            params.append(p)
        for i in range(0, len(params), UPDATE_BATCH):
            self.session.execute(update(FeatureSnapshot), params[i : i + UPDATE_BATCH])
        return len(params)


__all__ = [
    "PRICE_COLUMNS",
    "SHORT_TERM_COLUMNS",
    "ShortTermBackfill",
    "compute_short_term_frame",
    "load_short_term_inputs",
    "short_term_features_at",
]
