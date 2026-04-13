"""Operations API routes.

Provides scheduler control, backfill management, DB maintenance,
and system configuration endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db, get_scheduler
from trading_signals.api.job_tracker import job_tracker
from trading_signals.api.schemas import (
    AlembicStatus,
    BackfillStatus,
    DbTableInfo,
    SchedulerJobInfo,
    TriggerResponse,
)
from trading_signals.api.tasks import backfill_manager

router = APIRouter(prefix="/ops")


# ── Scheduler ────────────────────────────────────────────────────────────

@router.get("/scheduler", response_model=list[SchedulerJobInfo])
def get_scheduler_jobs(scheduler=Depends(get_scheduler)):
    """Get all scheduled jobs with their status and next run time."""
    if not scheduler or not scheduler.running:
        return []

    return [
        SchedulerJobInfo(
            id=job.id,
            name=job.name,
            trigger=str(job.trigger),
            next_run=job.next_run_time,
            pending=job.pending,
            is_running=job_tracker.is_running(job.id),
        )
        for job in scheduler.get_jobs()
    ]


@router.post("/scheduler/{job_id}/trigger", response_model=TriggerResponse)
def trigger_job(job_id: str, scheduler=Depends(get_scheduler)):
    """Manually trigger a scheduled job to run immediately."""
    if not scheduler or not scheduler.running:
        raise HTTPException(status_code=503, detail="Scheduler is not running")

    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    # Modify the job to run now (next tick)
    job.modify(next_run_time=None)
    scheduler.modify_job(job_id, next_run_time=None)

    return TriggerResponse(
        success=True,
        message=f"Job '{job.name}' triggered for immediate execution",
    )


# ── Alembic ──────────────────────────────────────────────────────────────

@router.get("/alembic", response_model=AlembicStatus)
def get_alembic_status(db: Session = Depends(get_db)):
    """Get current Alembic migration status."""
    try:
        result = db.execute(
            text("SELECT version_num FROM signals.alembic_version LIMIT 1")
        ).first()
        current = result[0] if result else None

        return AlembicStatus(
            current_revision=current,
            head_revision=None,  # Would need Alembic config to determine
            is_up_to_date=current is not None,
        )
    except Exception:
        return AlembicStatus(
            current_revision=None,
            is_up_to_date=False,
        )


# ── Backfill ─────────────────────────────────────────────────────────────

@router.post("/backfill/prices", response_model=TriggerResponse)
def start_price_backfill(
    start_date: str = Query("2021-01-01", description="Start date (ISO format)"),
):
    """Start historical price backfill from Alpaca.

    This is a long-running operation that runs in the background.
    Poll /backfill/status to track progress.
    """
    try:
        task_id = backfill_manager.start_price_backfill(start_date)
        return TriggerResponse(
            success=True,
            message=f"Price backfill started from {start_date}",
            task_id=task_id,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/backfill/indicators", response_model=TriggerResponse)
def start_indicator_backfill():
    """Start technical indicator backfill (recompute from prices).

    This is a long-running operation that runs in the background.
    Poll /backfill/status to track progress.
    """
    try:
        task_id = backfill_manager.start_indicator_backfill()
        return TriggerResponse(
            success=True,
            message="Technical indicator backfill started",
            task_id=task_id,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/backfill/status", response_model=list[BackfillStatus])
def get_backfill_status():
    """Get status of all backfill operations."""
    tasks = backfill_manager.get_all_status()
    return [
        BackfillStatus(
            task_id=t.task_id,
            operation=t.operation,
            status=t.status.value,
            progress_pct=t.progress_pct,
            current_ticker=t.current_ticker,
            started_at=t.started_at,
            eta_seconds=t.eta_seconds,
            error=t.error,
        )
        for t in tasks
    ]


@router.post("/backfill/sectors", response_model=TriggerResponse)
def start_sector_enrichment():
    """Start sector/industry enrichment from yfinance.

    Fetches missing sector and industry data for all active tickers
    that lack this information. Long-running background operation.
    Poll /backfill/status to track progress.
    """
    try:
        task_id = backfill_manager.start_sector_enrichment()
        return TriggerResponse(
            success=True,
            message="Sector enrichment started",
            task_id=task_id,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── Database Operations ──────────────────────────────────────────────────

@router.get("/db/stats", response_model=list[DbTableInfo])
def get_db_stats(db: Session = Depends(get_db)):
    """Get database table sizes and row counts."""
    try:
        result = db.execute(text("""
            SELECT
                relname AS table_name,
                n_live_tup AS row_count,
                pg_total_relation_size(
                    quote_ident(schemaname) || '.'
                    || quote_ident(relname)
                ) AS size_bytes
            FROM pg_stat_user_tables
            WHERE schemaname = 'signals'
            ORDER BY n_live_tup DESC
        """)).fetchall()

        return [
            DbTableInfo(
                table_name=row[0],
                row_count=row[1] or 0,
                size_bytes=row[2],
                size_human=_format_bytes(row[2]) if row[2] else None,
            )
            for row in result
        ]
    except Exception:
        return []


@router.post("/db/vacuum", response_model=TriggerResponse)
def run_vacuum(db: Session = Depends(get_db)):
    """Run VACUUM ANALYZE on the signals schema.

    Note: VACUUM cannot run inside a transaction block,
    so this uses autocommit mode.
    """
    try:
        # VACUUM requires autocommit, so we use raw connection
        from trading_signals.db.session import get_engine
        engine = get_engine()
        with engine.connect() as conn:
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(text("VACUUM ANALYZE"))

        return TriggerResponse(
            success=True,
            message="VACUUM ANALYZE completed successfully",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"VACUUM failed: {e}")


@router.post("/db/reset", response_model=TriggerResponse)
def reset_database(db: Session = Depends(get_db)):
    """Reset ALL data tables to empty state (factory reset).

    Truncates all data tables in the signals schema EXCEPT
    the universe table (ticker definitions) and alembic_version.
    This is a destructive operation!
    """
    try:
        # Tables to truncate (order matters for FK constraints)
        tables_to_reset = [
            "signals.technical_indicators",
            "signals.analyst_ratings",
            "signals.earnings_calendar",
            "signals.fundamentals_snapshot",
            "signals.politician_trades",
            "signals.form13f_holdings",
            "signals.insider_clusters",
            "signals.insider_trades",
            "signals.ark_deltas",
            "signals.ark_holdings",
            "signals.prices_daily",
            "signals.collection_log",
        ]

        total_deleted = 0
        for table in tables_to_reset:
            result = db.execute(text(f"DELETE FROM {table}"))
            total_deleted += result.rowcount

        db.commit()

        return TriggerResponse(
            success=True,
            message=(
                f"Factory reset completed. {total_deleted} records deleted "
                f"from {len(tables_to_reset)} tables. "
                f"Universe ({len(tables_to_reset)} tables) preserved."
            ),
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Reset failed: {e}")


def _format_bytes(size: int) -> str:
    """Format bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"
