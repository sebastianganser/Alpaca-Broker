"""Synthetic session + fake loader for the context-pack tests (no DB).

:func:`make_loader` builds a ``PackLoader`` with a cross-section of ``n``
tickers (all ``FEATURE_COLUMNS``, 11 sectors), fundamentals, closes and
raw-table rows for every ticker, *including deliberately leaky rows*
(a Form 4 accepted 16:05 ET on the session, news published after 16:00 ET,
a 13F filed after the session, a settlement inside the publication lag ...)
so the tests can prove that the generator hides them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tests.unit.screen_data import cross_section
from trading_signals.db.models.features import FEATURE_COLUMNS
from trading_signals.derived.context_pack.model import UniverseInfo
from trading_signals.derived.context_pack.rows import (
    ArkRow,
    EarningsRow,
    EstimateRow,
    HeadlineRow,
    HolderRow,
    Holders13F,
    InsiderTradeRow,
    IvRow,
    MembershipRow,
    PoliticianRow,
    RatingRow,
    RawSources,
    ShortInterestRow,
    SourceStat,
    TechnicalRow,
)

NY = ZoneInfo("America/New_York")
SESSION = date(2026, 10, 16)  # a Friday, NYSE session
SECTORS = (
    "Technology",
    "Healthcare",
    "Financial Services",
    "Industrials",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Energy",
    "Utilities",
    "Real Estate",
    "Basic Materials",
    "Communication Services",
)
FUND_COLUMNS = (
    "market_cap",
    "pe_ratio",
    "forward_pe",
    "ps_ratio",
    "pb_ratio",
    "ev_ebitda",
    "profit_margin",
    "operating_margin",
    "return_on_equity",
    "revenue_ttm",
    "revenue_growth_yoy",
    "eps_ttm",
    "eps_growth_yoy",
    "debt_to_equity",
    "current_ratio",
    "dividend_yield",
    "beta",
    "target_price_low",
    "target_price_mean",
    "target_price_median",
    "target_price_high",
)
SPARSE_PREFIXES = ("ark_", "politician_")
BOOL_COLUMNS = ("ark_multi_etf_signal", "insider_cluster_active")


def make_snapshots(n: int = 120, seed: int = 0, day: date = SESSION) -> pd.DataFrame:
    """One session of snapshots with every feature column filled."""
    rng = np.random.default_rng(seed)
    df = cross_section(day.isoformat(), n=n, seed=seed)
    df = df.drop(columns=["z"])
    df["snapshot_date"] = day
    for col in FEATURE_COLUMNS:
        if col in df.columns:
            continue
        if col in BOOL_COLUMNS:
            vals = rng.random(n) < 0.1
            df[col] = pd.Series(vals, dtype=object)
        elif col.startswith(SPARSE_PREFIXES):
            vals = rng.normal(size=n)
            vals[rng.random(n) < 0.9] = np.nan
            df[col] = vals
        elif col == "macro_vix_regime":
            df[col] = 1.0
        elif col.startswith(("macro_", "breadth_", "market_sentiment")):
            df[col] = {
                "macro_vix": 17.8,
                "macro_yield_spread": 0.45,
                "macro_hy_spread": 3.1,
                "macro_dollar_index": 120.3,
                "macro_inflation_expectation": 2.3,
                "breadth_advance_decline": 0.52,
                "breadth_pct_above_sma50": 0.61,
                "market_sentiment_7d": 0.12,
            }.get(col, 0.0)
        elif col == "earnings_days_until":
            df[col] = rng.integers(1, 90, size=n).astype(float)
        else:
            df[col] = rng.normal(size=n)
    # price_vs_sma50 present for everybody (history gate)
    df["dollar_volume_20d"] = rng.uniform(6e6, 5e9, size=n)
    return df


def make_fundamentals(
    tickers: list[str], seed: int = 1, day: date = SESSION
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = {c: rng.uniform(0.01, 100.0, size=len(tickers)) for c in FUND_COLUMNS}
    data["market_cap"] = rng.uniform(1e9, 4e12, size=len(tickers))
    data["dividend_yield"] = rng.uniform(0.0, 0.05, size=len(tickers))
    data["ticker"] = tickers
    data["snapshot_date"] = day - timedelta(days=2)
    data["most_recent_quarter"] = date(2026, 6, 30)
    return pd.DataFrame(data).set_index("ticker", drop=False)


def make_raw(ticker: str, day: date = SESSION, seed: int = 0) -> RawSources:
    """Raw-table rows of one ticker, with leaky rows mixed in."""
    close_et = datetime.combine(day, datetime.min.time().replace(hour=16), tzinfo=NY)
    r = RawSources()
    t = r.get(ticker)
    t.insider = [
        InsiderTradeRow(
            ticker,
            "Jane Doe",
            "CEO",
            "P",
            day - timedelta(days=5),
            day - timedelta(days=3),
            close_et - timedelta(days=3),
            1000,
            10.0,
            10000.0,
        ),
        InsiderTradeRow(
            ticker,
            "John Roe",
            "CFO",
            "S",
            day - timedelta(days=9),
            day - timedelta(days=7),
            None,
            5000,
            11.0,
            55000.0,
        ),
        # accepted 16:05 ET on the session day: NOT public at the close
        InsiderTradeRow(
            ticker,
            "LEAK Insider",
            "Director",
            "P",
            day,
            day,
            close_et + timedelta(minutes=5),
            1,
            1.0,
            1.0,
        ),
        # accepted 15:55 ET: public
        InsiderTradeRow(
            ticker,
            "Early Bird",
            "Director",
            "P",
            day,
            day,
            close_et - timedelta(minutes=5),
            2,
            2.0,
            4.0,
        ),
    ]
    t.ratings = [
        RatingRow(
            ticker,
            day - timedelta(days=4),
            "Big Bank",
            "up",
            "Hold",
            "Buy",
            90.0,
            110.0,
        ),
        RatingRow(
            ticker,
            day + timedelta(days=1),
            "LEAK Firm",
            "down",
            "Buy",
            "Sell",
            None,
            None,
        ),
    ]
    t.politicians = [
        PoliticianRow(
            ticker,
            "Pat Politician",
            "House",
            "D",
            "CA",
            "Purchase",
            day - timedelta(days=40),
            day - timedelta(days=2),
            "$1,001 - $15,000",
            "Self",
        ),
        PoliticianRow(
            ticker,
            "LEAK Senator",
            "Senate",
            "R",
            "TX",
            "Purchase",
            day,
            day + timedelta(days=1),
            "$1,001 - $15,000",
            "Self",
        ),
    ]
    pub = datetime.combine(day, datetime.min.time().replace(hour=10), tzinfo=NY)
    t.headlines = [
        HeadlineRow(
            ticker, pub, f"{ticker} beats expectations", "Benzinga", "positive", 0.91
        ),
        HeadlineRow(
            ticker, pub - timedelta(days=1), "Older story", "Reuters", "neutral", 0.02
        ),
        # 15:30 ET on the session day (21:30 Berlin): still public
        HeadlineRow(
            ticker,
            close_et - timedelta(minutes=30),
            "Late afternoon story",
            "Wire",
            "neutral",
            0.1,
        ),
        # published 16:30 ET on the session day: after the cutoff
        HeadlineRow(
            ticker,
            close_et + timedelta(minutes=30),
            "LEAK after the bell",
            "Benzinga",
            "negative",
            -0.8,
        ),
    ]
    t.earnings = [
        EarningsRow(
            ticker,
            day + timedelta(days=30),
            "AMC",
            1.5,
            None,
            None,
            datetime(2026, 9, 1),
        ),
        EarningsRow(ticker, day - timedelta(days=60), "AMC", 1.2, 1.3, 8.3),
        EarningsRow(ticker, day - timedelta(days=150), "BMO", 1.0, 1.1, 10.0),
        # first seen only after the session: an announced date nobody knew yet
        EarningsRow(
            ticker,
            day + timedelta(days=5),
            "AMC",
            9.9,
            None,
            None,
            datetime.combine(day + timedelta(days=2), datetime.min.time()),
        ),
    ]
    t.estimates = [
        EstimateRow(
            ticker,
            day,
            p,
            eps_avg=1.5,
            eps_n_analysts=20,
            eps_current=1.5,
            eps_7d_ago=1.48,
            eps_30d_ago=1.4,
            eps_90d_ago=1.3,
            rev_up_7d=3,
            rev_down_7d=0,
            rev_up_30d=6,
            rev_down_30d=1,
            revenue_avg=2.5e10,
            revenue_growth=0.12,
        )
        for p in ("0q", "+1q", "0y", "+1y")
    ] + [EstimateRow(ticker, day + timedelta(days=1), "0q", eps_avg=99.0)]
    t.short_interest = [
        ShortInterestRow(ticker, date(2026, 9, 30), 12_000_000, 2.1),
        ShortInterestRow(ticker, date(2026, 9, 15), 11_000_000, 1.9),
        # inside the 9-session publication lag: not public yet
        ShortInterestRow(ticker, date(2026, 10, 15), 99_000_000, 9.9),
    ]
    t.ark = [ArkRow(ticker, day, "ARKK", 2.5, 1000.0)]
    t.iv = [
        IvRow(ticker, day - timedelta(days=i), 0.30 + 0.001 * i, 0.02, -0.01, 0.8)
        for i in range(40)
    ]
    t.technical = TechnicalRow(ticker, day, 1.2, 0.9, 0.3)
    t.memberships = [
        MembershipRow(ticker, "sp500", date(2010, 3, 1)),
        MembershipRow(ticker, "nasdaq100", date(2026, 1, 1), source="initial_seed"),
    ]
    t.holders = Holders13F(
        report_period=date(2026, 6, 30),
        max_filing_date=date(2026, 8, 14),
        n_filers=1500,
        shares_total=5e8,
        prev_period=date(2026, 3, 31),
        prev_n_filers=1450,
        prev_shares_total=4.9e8,
        top=(
            HolderRow("Vanguard Group", 8e7, 1.6e10, 7.5e7),
            HolderRow("BlackRock", 7e7, 1.4e10, 7e7),
            HolderRow("New Fund LP", 1e6, 2e8, None),
        ),
    )
    t.stats = {
        "prices": SourceStat(date(2021, 10, 18), day, 1260),
        "estimates": SourceStat(date(2026, 4, 2), day, 130),
        "options_iv": SourceStat(date(2026, 8, 10), day, 50),
        "short_volume": SourceStat(date(2026, 5, 1), day, 120),
        "news": SourceStat(date(2026, 4, 1), day, 100),
    }
    return r


class FakeLoader:
    """A :class:`PackLoader` serving synthetic data; records its calls."""

    def __init__(self, n: int = 120, seed: int = 0, day: date = SESSION):
        self.day = day
        self.snap = make_snapshots(n, seed, day)
        self.tickers = list(self.snap["ticker"])
        rng = np.random.default_rng(seed + 1)
        self.info = {
            t: UniverseInfo(
                f"Company {t}",
                SECTORS[int(rng.integers(len(SECTORS)))],
                f"Industry {i % 7}",
            )
            for i, t in enumerate(self.tickers)
        }
        self.info["SPY"] = UniverseInfo("SPDR S&P 500", "Benchmark", "ETF")
        self.fund = make_fundamentals(self.tickers, seed + 2, day)
        self.calls: list[str] = []
        self.raw_requests: list[list[str]] = []
        #: Whether ``pack_candidates`` already holds rows for the session.
        self.stored_candidates = False

    def has_pack_candidates(self, d: date) -> bool:
        self.calls.append("has_pack_candidates")
        return self.stored_candidates and d == self.day

    def snapshots(self, d: date) -> pd.DataFrame:
        self.calls.append("snapshots")
        return self.snap.copy() if d == self.day else self.snap.iloc[0:0].copy()

    def universe(self, tickers):
        self.calls.append("universe")
        return {t: self.info[t] for t in tickers if t in self.info}

    def fundamentals(self, d: date) -> pd.DataFrame:
        self.calls.append("fundamentals")
        return self.fund.copy()

    def closes(self, refs) -> pd.DataFrame:
        self.calls.append("closes")
        rng = np.random.default_rng(7)
        rows = []
        base = {t: 50.0 + 100.0 * rng.random() for t in [*self.tickers, "SPY"]}
        ordered = sorted(refs)
        for k, d in enumerate(ordered):
            for t, b in base.items():
                rows.append((t, d, b * (1.0 + 0.02 * k) * (1.0 + 0.01 * rng.normal())))
        return pd.DataFrame(rows, columns=["ticker", "trade_date", "close"])

    def raw_sources(self, d: date, tickers) -> RawSources:
        self.calls.append("raw_sources")
        self.raw_requests.append(list(tickers))
        out = RawSources()
        for t in tickers:
            out.by_ticker[t] = make_raw(t, d).get(t)
        return out


def make_loader(n: int = 120, seed: int = 0, day: date = SESSION) -> FakeLoader:
    return FakeLoader(n, seed, day)


UTC_NOW = datetime(2026, 10, 18, 4, 30, tzinfo=UTC)
