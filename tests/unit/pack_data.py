"""Synthetic session + fake loader for the context-pack tests (no DB).

:func:`make_loader` builds a ``PackLoader`` with a cross-section of ``n``
tickers (all ``FEATURE_COLUMNS``, 11 sectors), fundamentals, closes and
raw-table rows for every ticker, *including deliberately leaky rows*
(a Form 4 accepted 16:05 ET on the session, news published after 16:00 ET,
a 13F filed after the session, a settlement inside the publication lag ...)
so the tests can prove that the generator hides them.
"""

from __future__ import annotations

import zlib
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tests.unit.screen_data import cross_section
from trading_signals.db.models.features import FEATURE_COLUMNS
from trading_signals.derived.context_pack.model import UniverseInfo
from trading_signals.derived.context_pack.rows import (
    ArkRow,
    BarRow,
    EarningsRow,
    EstimateRow,
    HeadlineRow,
    HolderRow,
    Holders13F,
    InsiderTradeRow,
    IvRow,
    MembershipRow,
    PoliticianRow,
    QuarterRow,
    RatingRow,
    RawSources,
    SecFilingRow,
    SecTextRow,
    ShortInterestRow,
    SourceStat,
    TechnicalRow,
)
from trading_signals.utils.market_calendar import trading_days

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
    # the columns of migration 040 are drawn AFTER the older ones, so the values
    # the older tests rely on do not change
    n = len(tickers)
    data["shares_outstanding"] = rng.uniform(1e8, 2e10, size=n)
    data["float_shares"] = data["shares_outstanding"] * rng.uniform(0.7, 0.99, size=n)
    data["held_percent_institutions"] = rng.uniform(0.3, 1.1, size=n)
    data["held_percent_insiders"] = rng.uniform(0.0, 0.1, size=n)
    data["short_percent_of_float"] = rng.uniform(0.003, 0.12, size=n)
    data["free_cashflow"] = rng.uniform(-2e9, 9e10, size=n)
    data["total_cash"] = rng.uniform(1e8, 1e11, size=n)
    data["total_debt"] = rng.uniform(1e8, 1.5e11, size=n)
    data["gross_margins"] = rng.uniform(0.15, 0.8, size=n)
    data["recommendation_mean"] = rng.uniform(1.2, 3.6, size=n)
    data["number_of_analyst_opinions"] = rng.integers(5, 45, size=n).astype(float)
    data["recommendation_key"] = np.where(
        data["recommendation_mean"] < 2.5, "buy", "hold"
    )
    data["ticker"] = tickers
    data["snapshot_date"] = day - timedelta(days=2)
    data["most_recent_quarter"] = date(2026, 6, 30)
    return pd.DataFrame(data).set_index("ticker", drop=False)


def _weekday(d: date) -> date:
    """``d`` moved back to the previous Monday-Friday day."""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def make_bars(
    ticker: str,
    day: date = SESSION,
    days: int = 840,
    overrides: dict[date, float] | None = None,
    leak_after: bool = True,
) -> list[BarRow]:
    """Daily real bars on the NYSE sessions up to ``day`` (random walk, seeded).

    ``overrides`` replaces the close of single dates (the open / high / low
    follow). ``leak_after`` adds a bar AFTER ``day`` (a leaky loader), which
    the point-in-time filter must remove.
    """
    sessions = trading_days(day - timedelta(days=days), day)
    if leak_after:
        sessions = [*sessions, day + timedelta(days=3)]  # the next Monday
    rng = np.random.default_rng(zlib.crc32(ticker.encode()))
    rets = rng.normal(0.0004, 0.015, len(sessions))
    level = 60.0 + float(rng.uniform(0, 80))
    out = []
    prev = level
    for s, r in zip(sessions, rets, strict=True):
        close = prev * (1.0 + float(r))
        if overrides and s in overrides:
            close = overrides[s]
        open_ = prev * (1.0 + float(rng.normal(0, 0.004)))
        high = max(open_, close) * (1.0 + abs(float(rng.normal(0, 0.005))))
        low = min(open_, close) * (1.0 - abs(float(rng.normal(0, 0.005))))
        volume = float(rng.integers(1_000_000, 9_000_000))
        out.append(BarRow(s, open_, high, low, close, volume))
        prev = close
    return out


PRESS_RELEASE = (
    "ACME Corp. Reports Third Quarter 2026 Results. Revenue of $12.3 billion, up 8% "
    "year over year; diluted EPS of $1.45. The Board raised the quarterly dividend by "
    "5% and authorized an additional $5 billion share repurchase program. "
    "Management raised full-year guidance for revenue and earnings per share, citing "
    "strong demand in the data center segment and improved operating leverage. "
    "Conference call today at 5:00 p.m. Eastern. Forward-looking statements: this "
    "release contains forward-looking statements that involve risks and uncertainties."
)
FILLER = (
    " The company will provide further details in its next periodic report and on "
    "its investor relations website; the information in this report is furnished "
    "and not filed, and no forward-looking statement is a guarantee of future "
    "performance, results or events, which are subject to the risks described in "
    "the company's annual report and its other public filings."
)
BODY_502 = (
    "Item 5.02 Departure of Directors. The Board accepted the resignation of the "
    "Chief Financial Officer effective December 31." + FILLER
)
BODY_101 = (
    "Item 1.01 Entry into a Material Definitive Agreement. The company entered into "
    "a long-term supply agreement."
)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY)


def make_sec_filings(ticker: str, day: date = SESSION) -> list[SecFilingRow]:
    """Nine listed filings (one more than the table shows), four Form 144 notices
    and three leaky rows: a filing accepted 16:05 ET on the session, one filed
    after the session and one just outside the 90-day window."""

    def row(back, hour, form, items=(), texts=(), amend=False, acc=None, minute=0):
        filed = day - timedelta(days=back)
        return SecFilingRow(
            ticker=ticker,
            accession_number=acc or f"0000000000-26-{back:06d}{hour:02d}",
            form=form,
            filing_date=filed,
            acceptance=_at(filed, hour, minute),
            items=tuple(items),
            is_amendment=amend,
            texts=tuple(SecTextRow(e, x) for e, x in texts),
        )

    return [
        row(3, 17, "8-K", ("2.02", "9.01"), [("EX-99.1", PRESS_RELEASE)], minute=10),
        row(6, 8, "8-K", ("5.02",), [("8-K", BODY_502)], minute=5),
        row(
            9,
            9,
            "8-K",
            ("7.01", "9.01"),
            [("EX-99.1", "Investor presentation for the autumn conference." + FILLER)],
        ),
        row(12, 7, "8-K/A", ("2.02",), amend=True),
        row(18, 16, "SCHEDULE 13D", minute=30),
        row(25, 12, "S-3ASR"),
        row(30, 6, "10-Q"),
        row(40, 8, "8-K", ("1.01",), [("8-K", BODY_101)]),
        row(55, 8, "8-K", ("3.02",)),
        *[row(5 + 7 * k, 10, "144", acc=f"0000000001-26-{k:08d}") for k in range(4)],
        # accepted 16:05 ET on the session day: NOT public at the close
        row(
            0,
            16,
            "8-K",
            ("8.01",),
            [("EX-99.1", "LEAK press release after the bell")],
            acc="0000000002-26-LEAK0001",
            minute=5,
        ),
        # filed after the session: not public
        SecFilingRow(
            ticker,
            "0000000003-26-LEAK0002",
            "8-K",
            day + timedelta(days=1),
            _at(day + timedelta(days=1), 9),
            ("2.02",),
            False,
            (SecTextRow("EX-99.1", "LEAK next day press release"),),
        ),
        # older than the 90-day window
        row(100, 8, "8-K", ("2.02",), acc="0000000004-26-OLD00001"),
    ]


def make_quarters(ticker: str, day: date = SESSION) -> list[QuarterRow]:
    """Twelve reported quarters (newest ended 2026-06-30) plus one leaky quarter that
    was filed only after the session."""
    ends = [
        date(2026, 6, 30), date(2026, 3, 31), date(2025, 12, 31), date(2025, 9, 30),
        date(2025, 6, 30), date(2025, 3, 31), date(2024, 12, 31), date(2024, 9, 30),
        date(2024, 6, 30), date(2024, 3, 31), date(2023, 12, 31), date(2023, 9, 30),
    ]  # fmt: skip
    out = []
    for k, end in enumerate(ends):
        revenue = 1.0e10 * (1.0 + 0.02 * (len(ends) - k))
        out.append(
            QuarterRow(
                period_end=end,
                filed=end + timedelta(days=35),
                fiscal_period=f"Q{(end.month - 1) // 3 + 1}",
                form="10-K" if end.month == 12 else "10-Q",
                revenue=revenue,
                gross_profit=revenue * 0.52,
                operating_income=revenue * 0.28,
                net_income=revenue * 0.21,
                eps_diluted=1.0 + 0.05 * (len(ends) - k),
                operating_cash_flow=revenue * 0.30,
                capex=revenue * 0.05,
                free_cash_flow=revenue * 0.25,
                cash_and_equivalents=8.0e9 + 1e8 * k,
                total_debt=1.5e10,
                shares_outstanding=1.5e10 * (1.0 + 0.01 * k),
            )
        )
    out.append(
        QuarterRow(
            period_end=date(2026, 9, 30),
            filed=day + timedelta(days=2),  # published after the session
            fiscal_period="Q3",
            form="10-Q",
            revenue=9.999e10,
        )
    )
    return out


def make_raw(ticker: str, day: date = SESSION, seed: int = 0) -> RawSources:
    """Raw-table rows of one ticker, with leaky rows mixed in."""
    close_et = datetime.combine(day, datetime.min.time().replace(hour=16), tzinfo=NY)
    r = RawSources()
    t = r.get(ticker)
    filler_insider = [
        InsiderTradeRow(
            ticker,
            f"Filler Insider {k}",
            "VP Engineering",
            "P" if k % 2 else "S",
            day - timedelta(days=100 + 12 * k),
            day - timedelta(days=98 + 12 * k),
            None,
            500 * k,
            20.0,
            10000.0 * k,
            20000 * k,
        )
        for k in range(1, 5)
    ]
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
            11000,  # holding after the purchase: +10 % of the 10000 before
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
            45000,  # after the sale: -10 % of the 50000 before
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
        *filler_insider,
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
            "Raises",
        ),
        RatingRow(
            ticker,
            day - timedelta(days=9),
            "Other Bank",
            "main",
            "Buy",
            "Buy",
            120.0,
            120.0,
            "Maintains",
        ),
        RatingRow(
            ticker,
            day - timedelta(days=12),
            "No Target Bank",
            "main",
            "Hold",
            "Hold",
            None,
            None,
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
    t.ratings += [
        RatingRow(
            ticker,
            day - timedelta(days=40 + 15 * k),
            f"Filler Securities {k}",
            "main",
            "Buy",
            "Buy",
            100.0 + k,
            105.0 + k,
            "Raises",
        )
        for k in range(1, 4)
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
    t.politicians += [
        PoliticianRow(
            ticker,
            f"Filler Member {k}",
            "House",
            "R",
            "TX",
            "Purchase",
            day - timedelta(days=30 + 10 * k),
            day - timedelta(days=20 + 10 * k),
            "$15,001 - $50,000",
            "Spouse",
        )
        for k in range(1, 4)
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
        HeadlineRow(
            ticker,
            pub - timedelta(days=3),
            f"{ticker} announces new share buyback programme worth billions",
            "Reuters",
            "positive",
            0.7,
        ),
        HeadlineRow(
            ticker,
            pub - timedelta(days=6),
            "Regulator opens inquiry into industry pricing practices",
            "Bloomberg",
            "negative",
            -0.6,
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
        # six older quarters (BMO / AMC / DMH / unknown time), so the table
        # shows the full eight
        *[
            EarningsRow(
                ticker,
                _weekday(day - timedelta(days=150 + 91 * k)),
                ("AMC", "BMO", "DMH", None, "AMC", "BMO")[k - 1],
                1.0,
                1.0 + 0.05 * k,
                5.0 * k,
            )
            for k in range(1, 7)
        ],
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
            eps_low=1.3,
            eps_high=1.7,
            eps_year_ago=1.3,
            eps_growth=0.15,
            revenue_low=2.3e10,
            revenue_high=2.7e10,
            revenue_n_analysts=18,
            revenue_year_ago=2.2e10,
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
        n_filers=15,
        shares_total=5e8,
        prev_period=date(2026, 3, 31),
        prev_n_filers=14,
        prev_shares_total=4.9e8,
        n_tracked=19,
        prev_n_tracked=18,
        top=(
            HolderRow("Vanguard Group", 8e7, 1.6e10, 7.5e7),
            HolderRow("BlackRock", 7e7, 1.4e10, 7e7),
            HolderRow("New Fund LP", 1e6, 2e8, None),
            HolderRow("State Street Global Advisors", 4e7, 8e9, 4.1e7),
            HolderRow("Geode Capital Management", 2e7, 4e9, 1.9e7),
        ),
    )
    t.bars = make_bars(ticker, day)
    t.sec_filings = make_sec_filings(ticker, day)
    t.quarters = make_quarters(ticker, day)
    t.stats = {
        "sec_filings": SourceStat(day - timedelta(days=400), day, 80),
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


def stub_chart(ticker, company, session, bars, analysis):
    """A tiny PNG with the generator's ownership marker (fast stand-in for the
    real chart, which takes about 0.3 s per ticker; ``test_context_pack_chart.py``
    draws the real one)."""
    import io

    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    from trading_signals.derived.context_pack.chart import chart_description

    info = PngInfo()
    info.add_text("Description", chart_description(ticker, session))
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(out, format="PNG", pnginfo=info)
    return out.getvalue()
