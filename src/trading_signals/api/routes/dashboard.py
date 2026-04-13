"""Dashboard API routes.

Provides the main overview: collector status, table statistics,
and system health information.
"""

import time

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db, get_scheduler
from trading_signals.api.schemas import (
    CollectorStatus,
    DashboardSummary,
    SystemHealth,
    TableStats,
)
from trading_signals.db.models import (
    AnalystRating,
    ARKDelta,
    ARKHolding,
    CollectionLog,
    EarningsCalendar,
    Form13FHolding,
    FundamentalsSnapshot,
    InsiderCluster,
    InsiderTrade,
    PoliticianTrade,
    PriceDaily,
    TechnicalIndicator,
    Universe,
)

router = APIRouter(prefix="/dashboard")

# Track app start time for uptime calculation
_start_time = time.time()

# Table models with display names
_TABLE_MODELS = [
    ("universe", Universe, "ticker", None),
    ("prices_daily", PriceDaily, "trade_date", "trade_date"),
    ("ark_holdings", ARKHolding, "snapshot_date", "snapshot_date"),
    ("ark_deltas", ARKDelta, "delta_date", "delta_date"),
    ("insider_trades", InsiderTrade, "transaction_date", "transaction_date"),
    ("insider_clusters", InsiderCluster, "cluster_start", "cluster_start"),
    ("form13f_holdings", Form13FHolding, "report_period", "report_period"),
    ("politician_trades", PoliticianTrade, "transaction_date", "transaction_date"),
    ("fundamentals_snapshot", FundamentalsSnapshot, "snapshot_date", "snapshot_date"),
    ("analyst_ratings", AnalystRating, "rating_date", "rating_date"),
    ("earnings_calendar", EarningsCalendar, "earnings_date", "earnings_date"),
    ("technical_indicators", TechnicalIndicator, "trade_date", "trade_date"),
    ("collection_log", CollectionLog, "started_at", None),
]


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(
    db: Session = Depends(get_db),
    scheduler=Depends(get_scheduler),
):
    """Get the complete dashboard overview.

    Returns collector status, table row counts with date ranges,
    and system health information.
    """
    # ── Collector Status ─────────────────────────────────────────────
    collectors = []
    if scheduler and scheduler.running:
        for job in scheduler.get_jobs():
            # Get last run info from collection_log
            last_log = (
                db.query(CollectionLog)
                .filter(CollectionLog.collector_name == job.id)
                .order_by(CollectionLog.started_at.desc())
                .first()
            )

            collectors.append(
                CollectorStatus(
                    id=job.id,
                    name=job.name,
                    last_run=last_log.started_at if last_log else None,
                    last_status=last_log.status if last_log else None,
                    records_written=last_log.records_written if last_log else None,
                    next_run=job.next_run_time,
                )
            )

    # ── Table Statistics ─────────────────────────────────────────────
    table_stats = []
    for table_name, model, date_col_name, date_range_col in _TABLE_MODELS:
        try:
            row_count = db.query(func.count()).select_from(model).scalar() or 0

            min_date = None
            max_date = None
            if date_range_col:
                date_col = getattr(model, date_range_col, None)
                if date_col is not None:
                    result = db.query(
                        func.min(date_col), func.max(date_col)
                    ).first()
                    if result:
                        min_date = result[0]
                        max_date = result[1]

            table_stats.append(
                TableStats(
                    table=table_name,
                    row_count=row_count,
                    min_date=min_date,
                    max_date=max_date,
                )
            )
        except Exception:
            table_stats.append(
                TableStats(table=table_name, row_count=-1)
            )

    # ── System Health ────────────────────────────────────────────────
    db_connected = True
    alembic_rev = None
    try:
        db.execute(text("SELECT 1"))
        # Get current Alembic revision
        result = db.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        ).first()
        if result:
            alembic_rev = result[0]
    except Exception:
        db_connected = False

    system_health = SystemHealth(
        db_connected=db_connected,
        alembic_revision=alembic_rev,
        scheduler_running=scheduler is not None and scheduler.running,
        job_count=len(scheduler.get_jobs()) if scheduler else 0,
        uptime_seconds=time.time() - _start_time,
    )

    return DashboardSummary(
        collectors=collectors,
        table_stats=table_stats,
        system_health=system_health,
    )
