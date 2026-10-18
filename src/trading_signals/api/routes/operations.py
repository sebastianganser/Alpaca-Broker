"""Operations API routes.

Provides scheduler control, backfill management, DB maintenance,
and system configuration endpoints.
Mutating endpoints (POST) are protected by ``require_write_access``
(applied at router include in ``main.py``).
"""

import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
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
from trading_signals.scheduler.registry import MANUAL_SUFFIX
from trading_signals.utils.retention import data_start_date

router = APIRouter(prefix="/ops")

#: Tables kept by the factory reset: ticker definitions, the learned ETF filter
#: and the forecast evaluation records. The forecasts, their reviews, the seals
#: that prove they were on time, the frozen criterion samples with their
#: verdicts and the pack candidates are the record of a pre-registered
#: forward test (``docs/FORECASTS.md`` section 6): they cannot be re-collected
#: and a reset of the market data must not erase them.
FORECAST_RECORD_TABLES = frozenset(
    {
        "forecasts",
        "forecast_reviews",
        "forecast_criterion_samples",
        "pack_candidates",
        "forecast_file_seals",
    }
)
RESET_PRESERVED_TABLES = frozenset({"universe", "ticker_blacklist"}) | (
    FORECAST_RECORD_TABLES
)
RESET_CONFIRMATION = "RESET"


class ResetRequest(BaseModel):
    """Optional JSON body for ``POST /ops/db/reset``."""

    confirm: str | None = None


# ── Scheduler ────────────────────────────────────────────────────────────

@router.get("/scheduler", response_model=list[SchedulerJobInfo])
def get_scheduler_jobs(scheduler=Depends(get_scheduler)):
    """Get all scheduled jobs with their status and next run time.

    Chain steps (feature_pipeline, target_backfill, context_pack_generator)
    are paused (``next_run`` = null) and run via ``nightly_chain``.
    """
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
        if not job.id.endswith(MANUAL_SUFFIX)
    ]


@router.post("/scheduler/{job_id}/trigger", response_model=TriggerResponse)
def trigger_job(job_id: str, scheduler=Depends(get_scheduler)):
    """Manually trigger a scheduled job to run immediately."""
    if not scheduler or not scheduler.running:
        raise HTTPException(status_code=503, detail="Scheduler is not running")

    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    now = datetime.now(timezone.utc)
    if job.next_run_time is None:
        # Paused job (nightly-chain step): run a one-shot clone so the
        # original stays paused and doesn't start firing on its cron.
        scheduler.add_job(
            job.func,
            trigger="date",
            run_date=now,
            id=f"{job_id}{MANUAL_SUFFIX}",
            name=f"{job.name} (manuell)",
            replace_existing=True,
        )
    else:
        # Schedule job for immediate execution.
        # APScheduler's CronTrigger will automatically recalculate
        # the next regular run time after this execution completes.
        # NOTE: next_run_time=None would PAUSE the job, not run it!
        scheduler.modify_job(job_id, next_run_time=now)

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
    start_date: str | None = Query(
        None,
        description=(
            "Start date (ISO format). Default and minimum: start of the "
            "rolling retention window (data_start_date())."
        ),
    ),
):
    """Start historical price backfill from Alpaca.

    This is a long-running operation that runs in the background.
    Poll /backfill/status to track progress.
    """
    try:
        task_id = backfill_manager.start_price_backfill(start_date)
        effective = max(
            data_start_date().isoformat(), start_date or data_start_date().isoformat()
        )
        return TriggerResponse(
            success=True,
            message=f"Price backfill started from {effective}",
            task_id=task_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid start_date: {e}")
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

# ── Benchmark ETF Seeding (Sprint 9.5b B3) ──────────────────────────────

@router.post("/seed/benchmark-etfs", response_model=TriggerResponse)
def seed_benchmark_etfs(db: Session = Depends(get_db)):
    """Seed benchmark and sector ETFs into the universe.

    Adds all BENCHMARK_TICKERS (SPY, QQQ, IWM + 11 GICS sector SPDRs)
    to the universe table, removes them from the blacklist if present,
    and triggers a price backfill for any newly added tickers.

    Idempotent: safe to run multiple times.
    """
    from datetime import date

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from trading_signals.db.models.blacklist import TickerBlacklist
    from trading_signals.db.models.universe import Universe
    from trading_signals.universe.blacklist import BENCHMARK_TICKERS

    # ETF metadata (name, sector mapping)
    etf_info = {
        "SPY": ("SPDR S&P 500 ETF Trust", "Benchmark"),
        "QQQ": ("Invesco QQQ Trust", "Benchmark"),
        "IWM": ("iShares Russell 2000 ETF", "Benchmark"),
        "XLB": ("Materials Select Sector SPDR", "Materials"),
        "XLC": ("Communication Services Select Sector SPDR", "Communication Services"),
        "XLE": ("Energy Select Sector SPDR", "Energy"),
        "XLF": ("Financial Select Sector SPDR", "Financials"),
        "XLI": ("Industrial Select Sector SPDR", "Industrials"),
        "XLK": ("Technology Select Sector SPDR", "Information Technology"),
        "XLP": ("Consumer Staples Select Sector SPDR", "Consumer Staples"),
        "XLRE": ("Real Estate Select Sector SPDR", "Real Estate"),
        "XLU": ("Utilities Select Sector SPDR", "Utilities"),
        "XLV": ("Health Care Select Sector SPDR", "Health Care"),
        "XLY": ("Consumer Discretionary Select Sector SPDR", "Consumer Discretionary"),
    }

    added = []
    for ticker in sorted(BENCHMARK_TICKERS):
        name, sector = etf_info.get(ticker, (ticker, "Unknown"))

        # Remove from blacklist if present
        db.execute(
            TickerBlacklist.__table__.delete().where(
                TickerBlacklist.ticker == ticker
            )
        )

        # Upsert into universe
        stmt = (
            pg_insert(Universe)
            .values(
                ticker=ticker,
                company_name=name,
                sector=sector,
                added_date=date.today(),
                added_by="seed_benchmark_etfs",
                is_active=True,
            )
            .on_conflict_do_update(
                index_elements=["ticker"],
                set_={"is_active": True, "company_name": name, "sector": sector},
            )
        )
        result = db.execute(stmt)
        if result.rowcount > 0:
            added.append(ticker)

    db.commit()

    msg = f"Seeded {len(added)} benchmark ETFs"
    if added:
        msg += (
            f": {', '.join(added)}. Run /backfill/prices to backfill historical data."
        )
    else:
        msg += " (all already present)"

    return TriggerResponse(success=True, message=msg)


# ── Index Membership Seeding ─────────────────────────────────────────────

@router.post("/seed/index-membership", response_model=TriggerResponse)
def seed_index_membership(db: Session = Depends(get_db)):
    """Seed historical index membership data from the Wikipedia change tables.

    Parses the S&P 500 ("Historical components of the S&P 500") and Nasdaq 100
    historical changes from Wikipedia and generates membership intervals in
    the index_membership table. Ticker renames are normalized to the current
    symbol (no exits).

    Only a **complete** source (additions *and* removals) is accepted. If the
    change table cannot be parsed (layout change, fetch error) the request is
    refused with 502 and nothing is written: the additions-only fallback
    (current members' "Date added") silently hides every index exit, which is
    exactly the survivorship bias this table exists to prevent. The GitHub
    CSV is no longer a seeding source (snapshot dates, stale by weeks); it is
    a cross-check only (``GitHubSP500Backup``).

    This is a one-time operation that should be run after deploying
    the index_membership migration. Subsequent updates are handled
    automatically by the monthly IndexSyncer job.

    Idempotent: Running multiple times will not create duplicates
    (uses ON CONFLICT DO NOTHING via unique constraint).
    """
    from trading_signals.universe.wikipedia_index_history import (
        WikipediaIndexHistoryParser,
        seed_blockers,
    )
    from trading_signals.utils.logging import get_logger

    logger = get_logger("index_membership_seed")

    # 1. Parse the sources first (synchronously): an incomplete source must be
    #    reported to the caller, not discovered later in a background thread.
    parser = WikipediaIndexHistoryParser()
    sp500_result = parser.parse_sp500_changes()
    nasdaq_result = parser.parse_nasdaq100_changes()
    for w in sp500_result.warnings + nasdaq_result.warnings:
        logger.warning(w)

    blockers = seed_blockers(sp500_result, nasdaq_result)
    if blockers:
        detail = (
            "Index membership NOT seeded -- incomplete history source "
            "(no removal data would silently reintroduce survivorship bias): "
            + "; ".join(blockers)
        )
        logger.error(f"[seed] {detail}")
        raise HTTPException(status_code=502, detail=detail)

    def _run_seed():
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from trading_signals.db.models.index_membership import IndexMembership
        from trading_signals.db.session import get_session
        from trading_signals.universe.wikipedia_index_history import (
            generate_intervals_from_changes,
        )
        from trading_signals.utils.retention import data_start_date as _start

        all_changes = sp500_result.changes + nasdaq_result.changes

        # 2. Get current index members to fill gaps for long-standing members
        with get_session() as session:
            from trading_signals.universe.index_sync import IndexSyncer
            syncer = IndexSyncer(session)
            current_sp500 = syncer._fetch_sp500()
            current_nasdaq100 = syncer._fetch_nasdaq100()

        current_members = {
            "sp500": current_sp500,
            "nasdaq100": current_nasdaq100,
        }

        # 3. Generate intervals
        intervals = generate_intervals_from_changes(
            all_changes, current_members, _start()
        )

        # 4. Insert into database (idempotent)
        with get_session() as session:
            inserted = 0
            for iv in intervals:
                stmt = (
                    pg_insert(IndexMembership)
                    .values(**iv)
                    .on_conflict_do_nothing(
                        constraint="uq_membership_ticker_index_from"
                    )
                )
                result = session.execute(stmt)
                inserted += result.rowcount

        logger.info(
            f"[seed] Index membership seeding complete: "
            f"{inserted}/{len(intervals)} intervals inserted "
            f"({len(all_changes)} changes parsed)"
        )

    thread = threading.Thread(target=_run_seed, daemon=True)
    thread.start()

    return TriggerResponse(
        success=True,
        message="Index membership seeding started in background",
    )


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


def reset_table_names() -> list[str]:
    """All ORM tables in the signals schema except the preserved ones.

    Derived from SQLAlchemy metadata so new tables are never forgotten.
    Ordered dependents-first.
    """
    import trading_signals.db.models  # noqa: F401  (register all models)
    import trading_signals.db.models.short_interest  # noqa: F401  (not in __init__)
    from trading_signals.db.base import SCHEMA_NAME, Base

    return [
        f"{t.schema}.{t.name}"
        for t in reversed(Base.metadata.sorted_tables)
        if t.schema == SCHEMA_NAME and t.name not in RESET_PRESERVED_TABLES
    ]


@router.post("/db/reset", response_model=TriggerResponse)
def reset_database(
    confirm: str | None = Query(None, description="Must be 'RESET'"),
    body: ResetRequest | None = Body(None),
    db: Session = Depends(get_db),
):
    """Reset ALL data tables to empty state (factory reset).

    Truncates all data tables in the signals schema EXCEPT
    the universe table (ticker definitions), the ticker blacklist, the
    forecast evaluation records (``FORECAST_RECORD_TABLES``)
    and alembic_version.
    Requires ``?confirm=RESET`` or JSON body ``{"confirm": "RESET"}``.
    This is a destructive operation!
    """
    if confirm != RESET_CONFIRMATION and (
        body is None or body.confirm != RESET_CONFIRMATION
    ):
        raise HTTPException(
            status_code=400,
            detail="Bestätigung fehlt: confirm=RESET erforderlich",
        )

    tables_to_reset = reset_table_names()
    try:
        # One statement: atomic and FK-safe among the truncated tables
        db.execute(text(f"TRUNCATE TABLE {', '.join(tables_to_reset)}"))
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Reset failed: {e}")

    return TriggerResponse(
        success=True,
        message=(
            f"Factory reset completed. {len(tables_to_reset)} tables truncated. "
            f"Preserved: {', '.join(sorted(RESET_PRESERVED_TABLES))}, alembic_version."
        ),
    )


def _format_bytes(size: int) -> str:
    """Format bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"
