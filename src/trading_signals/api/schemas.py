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
    shares_delta: float | None = None
    weight_delta_bps: float | None = None
    pct_change: float | None = None
    is_new_position: bool = False
    is_closed_position: bool = False


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


# ── Operations Schemas ───────────────────────────────────────────────────

class SchedulerJobInfo(BaseModel):
    """Information about a scheduled job."""
    id: str
    name: str
    trigger: str
    next_run: datetime | None = None
    pending: bool = False


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
