"""Plain row containers of the raw-table sections (no ORM, no DB).

The loader fills them with a *superset* of what may be shown; the exact
point-in-time rule is applied afterwards by :mod:`.pit` (one place, unit
tested), so the sections only ever see rows that were public at the close of
the pack session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class InsiderTradeRow:
    """An open-market Form 4 trade (``P`` purchase / ``S`` sale)."""

    ticker: str
    name: str | None
    title: str | None
    transaction_type: str  # "P" | "S"
    transaction_date: date | None
    filing_date: date | None
    acceptance: datetime | None  # tz-aware (SEC acceptance timestamp)
    shares: float | None
    price: float | None
    value: float | None
    #: Holding after the transaction as reported on the Form 4 (None = not given).
    shares_owned_after: float | None = None


@dataclass(frozen=True)
class RatingRow:
    """An analyst rating action."""

    ticker: str
    rating_date: date
    firm: str | None
    action: str | None  # up | down | main | init | reit
    rating_old: str | None
    rating_new: str | None
    target_old: float | None
    target_new: float | None
    #: Yahoo ``priceTargetAction`` (Raises / Lowers / Maintains ...), only in raw_data.
    target_action: str | None = None


@dataclass(frozen=True)
class PoliticianRow:
    """A congressional trade disclosure."""

    ticker: str
    name: str | None
    chamber: str | None
    party: str | None
    state: str | None
    transaction_type: str | None
    transaction_date: date | None
    disclosure_date: date | None
    amount_range: str | None
    owner: str | None


@dataclass(frozen=True)
class HeadlineRow:
    """A scored news headline (FinBERT)."""

    ticker: str
    published_at: datetime  # tz-aware
    headline: str
    source: str | None
    label: str | None
    score: float | None


@dataclass(frozen=True)
class EarningsRow:
    """An earnings calendar entry."""

    ticker: str
    earnings_date: date
    time_of_day: str | None
    eps_estimate: float | None
    eps_actual: float | None
    surprise_pct: float | None
    first_seen: datetime | None = None


@dataclass(frozen=True)
class EstimateRow:
    """A consensus estimate snapshot of one period (``0q``, ``+1q``, ...)."""

    ticker: str
    as_of: date
    period: str
    eps_avg: float | None = None
    eps_n_analysts: int | None = None
    eps_current: float | None = None
    eps_7d_ago: float | None = None
    eps_30d_ago: float | None = None
    eps_90d_ago: float | None = None
    rev_up_7d: int | None = None
    rev_down_7d: int | None = None
    rev_up_30d: int | None = None
    rev_down_30d: int | None = None
    revenue_avg: float | None = None
    revenue_growth: float | None = None
    eps_low: float | None = None
    eps_high: float | None = None
    eps_year_ago: float | None = None
    eps_growth: float | None = None
    revenue_low: float | None = None
    revenue_high: float | None = None
    revenue_n_analysts: int | None = None
    revenue_year_ago: float | None = None


@dataclass(frozen=True)
class ShortInterestRow:
    """A FINRA short-interest settlement."""

    ticker: str
    settlement_date: date
    short_interest: float | None
    days_to_cover: float | None


@dataclass(frozen=True)
class ArkRow:
    """One ARK ETF holding snapshot row."""

    ticker: str
    snapshot_date: date
    etf: str
    weight_pct: float | None
    shares: float | None


@dataclass(frozen=True)
class IvRow:
    """An options IV snapshot."""

    ticker: str
    snapshot_date: date
    atm_iv_30d: float | None = None
    skew_25d: float | None = None
    term_slope: float | None = None
    put_call_oi: float | None = None


@dataclass(frozen=True)
class TechnicalRow:
    """The latest technical indicator row."""

    ticker: str
    trade_date: date
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None


@dataclass(frozen=True)
class MembershipRow:
    """An index membership interval that covers the session."""

    ticker: str
    index_name: str
    valid_from: date
    valid_to: date | None = None  # first non-member day, None = still a member
    source: str | None = None


@dataclass(frozen=True)
class HolderRow:
    """A 13F holder of the latest public report period."""

    filer_name: str | None
    shares: float | None
    value: float | None
    prev_shares: float | None  # None = not a holder in the previous period


@dataclass(frozen=True)
class Holders13F:
    """13F summary of a ticker: latest public period vs the one before."""

    report_period: date
    max_filing_date: date  # latest filing of the period that counts (<= session)
    n_filers: int
    shares_total: float | None
    prev_period: date | None = None
    prev_n_filers: int | None = None
    prev_shares_total: float | None = None
    top: tuple[HolderRow, ...] = ()
    #: Distinct 13F filers of the whole tracked set that filed for the period
    #: (the "N" of "x of N tracked funds"); None = not loaded.
    n_tracked: int | None = None
    prev_n_tracked: int | None = None


@dataclass(frozen=True)
class BarRow:
    """A real (not gap-filled) daily price bar."""

    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None


@dataclass(frozen=True)
class SourceStat:
    """First / last observation and number of observation days of a source."""

    first: date | None = None
    last: date | None = None
    n_obs: int = 0


@dataclass(frozen=True)
class SecTextRow:
    """A stored text excerpt of a filing (press release ``EX-99.x`` or the 8-K body)."""

    exhibit: str
    excerpt: str


@dataclass(frozen=True)
class SecFilingRow:
    """An SEC EDGAR filing of the company (metadata, plus texts if stored)."""

    ticker: str
    accession_number: str
    form: str
    filing_date: date
    acceptance: datetime | None  # tz-aware EDGAR acceptance instant (None = unknown)
    items: tuple[str, ...] = ()  # 8-K item codes, e.g. ("2.02", "9.01")
    is_amendment: bool = False
    texts: tuple[SecTextRow, ...] = ()


@dataclass(frozen=True)
class QuarterRow:
    """A standardised fiscal quarter from the SEC XBRL facts (USD, as first reported).

    ``filed`` is the date the row became public (point-in-time key). A figure that
    is ``None`` is not reported / not derivable - never zero.
    """

    period_end: date
    filed: date
    fiscal_period: str | None = None  # Q1 ... Q4
    form: str | None = None  # 10-Q | 10-K
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    eps_diluted: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None
    free_cash_flow: float | None = None
    cash_and_equivalents: float | None = None
    total_debt: float | None = None
    shares_outstanding: float | None = None


@dataclass(frozen=True)
class PeerRow:
    """One line of the peer table (values of the pack session; None = no coverage)."""

    ticker: str
    company: str | None
    market_cap: float | None
    ret_60d: float | None
    forward_pe: float | None
    eps_revision_30d: float | None


@dataclass(frozen=True)
class PeerTable:
    """The peer section of one ticker: the ticker itself and its peers."""

    industry: str | None
    sector: str | None
    n_industry_pool: int  # other gate tickers in the same industry
    n_from_industry: int
    n_from_sector: int
    own: PeerRow
    peers: tuple[PeerRow, ...]


@dataclass
class TickerRaw:
    """All raw-table rows of ONE ticker (PIT-filtered by :mod:`.pit`)."""

    insider: list[InsiderTradeRow] = field(default_factory=list)
    ratings: list[RatingRow] = field(default_factory=list)
    politicians: list[PoliticianRow] = field(default_factory=list)
    headlines: list[HeadlineRow] = field(default_factory=list)
    earnings: list[EarningsRow] = field(default_factory=list)
    estimates: list[EstimateRow] = field(default_factory=list)
    short_interest: list[ShortInterestRow] = field(default_factory=list)
    ark: list[ArkRow] = field(default_factory=list)
    iv: list[IvRow] = field(default_factory=list)
    technical: TechnicalRow | None = None
    memberships: list[MembershipRow] = field(default_factory=list)
    holders: Holders13F | None = None
    #: Real daily bars, ascending, up to the pack session (chart, ranges,
    #: volatility, earnings reactions).
    bars: list[BarRow] = field(default_factory=list)
    #: SEC filings of the last 90 days (any order; PIT-filtered by :mod:`.pit`).
    sec_filings: list[SecFilingRow] = field(default_factory=list)
    #: Quarterly financials published by the pack day (any order).
    quarters: list[QuarterRow] = field(default_factory=list)
    stats: dict[str, SourceStat] = field(default_factory=dict)


@dataclass
class RawSources:
    """Raw-table rows of the selected tickers, keyed by ticker."""

    by_ticker: dict[str, TickerRaw] = field(default_factory=dict)

    def get(self, ticker: str) -> TickerRaw:
        return self.by_ticker.setdefault(ticker, TickerRaw())
