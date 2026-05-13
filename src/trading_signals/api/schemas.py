"""Pydantic schemas for API responses.

Separates API response shapes from ORM models to maintain
clean boundaries between the database and API layers.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field

# ── Dashboard Schemas ────────────────────────────────────────────────────

class CollectorStatus(BaseModel):
    """Status of a single scheduled collector job."""
    id: str
    name: str
    last_run: datetime | None = None
    last_status: str | None = None
    records_written: int | None = None
    next_run: datetime | None = None
    is_running: bool = False


class TableStats(BaseModel):
    """Row count and date range for a database table."""
    table: str
    row_count: int
    min_date: date | None = None
    max_date: date | None = None


class SystemHealth(BaseModel):
    """System health information."""
    db_connected: bool
    alembic_revision: str | None = None
    scheduler_running: bool
    job_count: int
    uptime_seconds: float | None = None


class DashboardSummary(BaseModel):
    """Complete dashboard overview response."""
    collectors: list[CollectorStatus]
    table_stats: list[TableStats]
    system_health: SystemHealth


# ── Universe Schemas ─────────────────────────────────────────────────────

class TickerSummary(BaseModel):
    """Ticker in the universe listing."""
    ticker: str
    company_name: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    is_active: bool
    added_date: date | None = None
    added_by: str | None = None
    index_membership: list[str] = Field(default_factory=list)
    last_price: float | None = None
    last_price_date: date | None = None


class UniverseResponse(BaseModel):
    """Paginated universe listing."""
    tickers: list[TickerSummary]
    total: int
    page: int
    limit: int


class TickerDetail(BaseModel):
    """Detailed ticker view with latest data."""
    ticker: str
    company_name: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    is_active: bool
    added_date: date | None = None
    added_by: str | None = None
    index_membership: list[str] = Field(default_factory=list)
    last_price: float | None = None
    last_price_date: date | None = None
    price_change_pct: float | None = None


# ── Signal Schemas ───────────────────────────────────────────────────────

class ARKDeltaItem(BaseModel):
    """Single ARK delta entry."""
    delta_date: date
    etf_ticker: str
    ticker: str
    delta_type: str  # new_position, closed, increased, decreased
    shares_delta: float | None = None
    shares_prev: float | None = None
    shares_curr: float | None = None
    weight_delta: float | None = None
    weight_prev: float | None = None
    weight_curr: float | None = None


class ARKSummaryItem(BaseModel):
    """Aggregated ARK delta per ticker across all ETFs over a time window."""
    ticker: str
    total_shares_delta: float
    total_weight_delta_bps: float  # Sum of weight deltas in basis points
    n_etfs: int                    # Number of ETFs with activity
    n_days: int                    # Number of days with activity
    etfs: list[str]                # Which ETFs were involved
    direction: str                 # 'increased', 'decreased', 'mixed'
    first_date: date
    last_date: date


class InsiderClusterItem(BaseModel):
    """Active insider cluster."""
    ticker: str
    cluster_start: date
    cluster_end: date
    n_insiders: int
    n_buys: int
    n_sells: int
    total_buy_value: float | None = None
    cluster_score: float | None = None


class PoliticianTradeItem(BaseModel):
    """Politician trade entry."""
    politician_name: str
    party: str | None = None
    ticker: str | None = None
    transaction_date: date | None = None
    disclosure_date: date | None = None
    transaction_type: str | None = None
    amount_range: str | None = None
    delay_days: int | None = None  # Days between trade and disclosure


class AnalystRatingItem(BaseModel):
    """Analyst rating change."""
    ticker: str
    firm: str | None = None
    rating_date: date | None = None
    rating_new: str | None = None
    rating_old: str | None = None
    action: str | None = None
    price_target_new: float | None = None
    price_target_old: float | None = None


# ── Ticker Detail Schemas ────────────────────────────────────────────────

class PricePoint(BaseModel):
    """Single OHLCV price data point."""
    trade_date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None


class IndicatorPoint(BaseModel):
    """Technical indicators for a single date."""
    trade_date: date
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    ema_12: float | None = None
    ema_26: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    bollinger_upper: float | None = None
    bollinger_lower: float | None = None
    atr_14: float | None = None
    volume_sma_20: float | None = None
    relative_strength_spy: float | None = None


class FundamentalsData(BaseModel):
    """Latest fundamentals snapshot."""
    snapshot_date: date | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    ps_ratio: float | None = None
    pb_ratio: float | None = None
    ev_ebitda: float | None = None
    profit_margin: float | None = None
    operating_margin: float | None = None
    return_on_equity: float | None = None
    revenue_growth_yoy: float | None = None
    eps_ttm: float | None = None
    debt_to_equity: float | None = None
    dividend_yield: float | None = None
    beta: float | None = None


# ── Data Quality Schemas ─────────────────────────────────────────────────

class DataQualityDimension(BaseModel):
    """Single data quality dimension status for a ticker."""
    label: str
    status: str  # "complete", "partial", "missing"
    summary: str
    detail: str | None = None


class TickerDataQuality(BaseModel):
    """Complete data quality assessment for a ticker."""
    ticker: str
    dimensions: list[DataQualityDimension]
    overall_completeness: float = 0.0  # 0.0 - 1.0


# ── Operations Schemas ───────────────────────────────────────────────────

class SchedulerJobInfo(BaseModel):
    """Information about a scheduled job."""
    id: str
    name: str
    trigger: str
    next_run: datetime | None = None
    pending: bool = False
    is_running: bool = False


class AlembicStatus(BaseModel):
    """Current Alembic migration status."""
    current_revision: str | None = None
    head_revision: str | None = None
    is_up_to_date: bool = True


class BackfillStatus(BaseModel):
    """Status of a running backfill operation."""
    task_id: str
    operation: str
    status: str  # "idle", "running", "completed", "failed"
    progress_pct: float = 0.0
    current_ticker: str | None = None
    started_at: datetime | None = None
    eta_seconds: float | None = None
    error: str | None = None


class DbTableInfo(BaseModel):
    """Database table size and statistics."""
    table_name: str
    row_count: int
    size_bytes: int | None = None
    size_human: str | None = None


class TriggerResponse(BaseModel):
    """Response when triggering a job or operation."""
    success: bool
    message: str
    task_id: str | None = None


# ── Logs Schemas ─────────────────────────────────────────────────────────

class CollectionLogItem(BaseModel):
    """Single collection log entry."""
    id: int
    collector_name: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str | None = None
    records_fetched: int | None = None
    records_written: int | None = None
    gaps_detected: int = 0
    gaps_repaired: int = 0
    gaps_extrapolated: int = 0
    errors: dict | None = None
    notes: str | None = None
    log_lines: list[dict] | None = None
    duration_seconds: float | None = None


class LogsResponse(BaseModel):
    """Paginated collection logs response."""
    logs: list[CollectionLogItem]
    total: int
    page: int
    limit: int


# ── Feature Pipeline Schemas ─────────────────────────────────────────────

class FeatureStats(BaseModel):
    """Feature pipeline statistics for the dashboard."""
    last_snapshot_date: date | None = None
    ticker_count: int = 0
    feature_coverage_pct: float = 0.0
    target_backfill_pct: float = 0.0
    total_snapshots: int = 0


class FeatureCoverageItem(BaseModel):
    """Coverage per ticker across feature groups (for heatmap)."""
    ticker: str
    ark: int = 0           # filled columns out of 11
    insider: int = 0       # filled columns out of 8
    analyst: int = 0       # filled columns out of 7
    politician: int = 0    # filled columns out of 4
    form13f: int = 0       # filled columns out of 2
    fundamentals: int = 0  # filled columns out of 8
    technical: int = 0     # filled columns out of 6
    earnings: int = 0      # filled columns out of 3
    sentiment: int = 0     # filled columns out of 6
    total_filled: int = 0  # sum of all filled features
    total_possible: int = 55  # total feature columns (49 + 6 sentiment)


class FeatureCoverageResponse(BaseModel):
    """Coverage matrix for all tickers on a given date."""
    snapshot_date: date | None = None
    items: list[FeatureCoverageItem] = []
    ticker_count: int = 0


class SignalConvergenceItem(BaseModel):
    """Ticker with active signal source count (multi-source overlap)."""
    ticker: str
    active_sources: int = 0
    source_names: list[str] = []
    # Key feature values for context
    ark_conviction_score: float | None = None
    insider_cluster_score: float | None = None
    analyst_rating_score: float | None = None
    rsi_14: float | None = None
    sentiment_avg_7d: float | None = None


class SignalConvergenceResponse(BaseModel):
    """Top tickers by signal convergence."""
    snapshot_date: date | None = None
    items: list[SignalConvergenceItem] = []


class HorizonStats(BaseModel):
    """Stats for a single return horizon."""
    horizon: str         # "1d", "5d", "20d", "60d"
    filled_count: int = 0
    total_count: int = 0
    filled_pct: float = 0.0
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    min_val: float | None = None
    max_val: float | None = None


class ReturnStatsResponse(BaseModel):
    """Aggregated forward return statistics."""
    horizons: list[HorizonStats] = []
    total_snapshots: int = 0


class FeatureGroupDetail(BaseModel):
    """Feature values for a single group."""
    group: str
    features: dict[str, float | int | bool | None] = {}
    filled: int = 0
    total: int = 0


class TickerFeatureDetail(BaseModel):
    """All features for a single ticker (latest snapshot)."""
    ticker: str
    snapshot_date: date | None = None
    groups: list[FeatureGroupDetail] = []
    total_filled: int = 0
    total_possible: int = 55
    # Target variables
    return_1d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None


# ── Sentiment Signal Schemas ─────────────────────────────────────────────

class SentimentSummaryItem(BaseModel):
    """Aggregated sentiment per ticker over a time window."""
    ticker: str
    avg_sentiment: float | None = None
    article_count: int = 0
    negative_count: int = 0
    positive_count: int = 0
    neutral_count: int = 0
    neg_pct: float = 0.0
    latest_headline: str | None = None
    latest_sentiment_label: str | None = None
    latest_date: date | None = None


class SentimentArticleItem(BaseModel):
    """Single news article with sentiment score."""
    article_id: str
    headline: str
    source: str | None = None
    published_at: datetime | None = None
    ticker: str | None = None
    sentiment_score: float | None = None
    sentiment_label: str | None = None
    url: str | None = None
