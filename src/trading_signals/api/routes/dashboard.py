"""Dashboard API routes.

Provides the main overview: collector status, table statistics,
and system health information.
"""

import time

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db, get_scheduler
from trading_signals.api.job_tracker import job_tracker
from trading_signals.api.schemas import (
    CollectorStatus,
    DashboardSummary,
    FeatureStats,
    SystemHealth,
    TableStats,
)
from trading_signals.db.models import (
    AnalysisReport,
    AnalystRating,
    ARKDelta,
    ARKHolding,
    CollectionLog,
    EarningsCalendar,
    EstimatesSnapshot,
    FeatureSnapshot,
    Form13FHolding,
    FundamentalsSnapshot,
    IndexMembership,
    InsiderCluster,
    InsiderTrade,
    MacroSeries,
    NewsArticle,
    NewsSentiment,
    OptionsIVSnapshot,
    PoliticianTrade,
    PriceDaily,
    TechnicalIndicator,
    TickerBlacklist,
    Universe,
)
from trading_signals.db.models.short_interest import ShortInterest, ShortVolume

router = APIRouter(prefix="/dashboard")

# Track app start time for uptime calculation
_start_time = time.time()

# Table models with display names
_TABLE_MODELS = [
    # ── Core ──
    ("universe", Universe, "ticker", None),
    ("prices_daily", PriceDaily, "trade_date", "trade_date"),
    ("technical_indicators", TechnicalIndicator, "trade_date", "trade_date"),
    # ── Fundamentals & Earnings ──
    ("fundamentals_snapshot", FundamentalsSnapshot, "snapshot_date", "snapshot_date"),
    ("analyst_ratings", AnalystRating, "rating_date", "rating_date"),
    ("earnings_calendar", EarningsCalendar, "earnings_date", "earnings_date"),
    ("estimates_snapshot", EstimatesSnapshot, "as_of", "as_of"),
    # ── Alternative Data ──
    ("ark_holdings", ARKHolding, "snapshot_date", "snapshot_date"),
    ("ark_deltas", ARKDelta, "delta_date", "delta_date"),
    ("insider_trades", InsiderTrade, "transaction_date", "transaction_date"),
    ("insider_clusters", InsiderCluster, "cluster_start", "cluster_start"),
    ("form13f_holdings", Form13FHolding, "report_period", "report_period"),
    ("politician_trades", PoliticianTrade, "disclosure_date", "disclosure_date"),
    # ── News & Sentiment ──
    ("news_articles", NewsArticle, "published_at", "published_at"),
    ("news_sentiment", NewsSentiment, "scored_at", "scored_at"),
    # ── Market Microstructure ──
    ("options_iv_snapshot", OptionsIVSnapshot, "snapshot_date", "snapshot_date"),
    ("short_volume", ShortVolume, "trade_date", "trade_date"),
    ("short_interest", ShortInterest, "settlement_date", "settlement_date"),
    # ── Macro & Derived ──
    ("macro_series", MacroSeries, "obs_date", "obs_date"),
    ("feature_snapshots", FeatureSnapshot, "snapshot_date", "snapshot_date"),
    ("analysis_reports", AnalysisReport, "report_date", "report_date"),
    # ── System ──
    ("index_membership", IndexMembership, "valid_from", None),
    ("ticker_blacklist", TickerBlacklist, "detected_at", None),
    ("collection_log", CollectionLog, "started_at", None),
]


@router.get("/feature-stats", response_model=FeatureStats)
def get_feature_stats(db: Session = Depends(get_db)):
    """Get feature pipeline statistics.

    Returns last snapshot date, ticker count, feature coverage,
    and target backfill completion percentage.
    """
    total = db.query(func.count()).select_from(FeatureSnapshot).scalar() or 0
    if total == 0:
        return FeatureStats()

    last_date = db.query(func.max(FeatureSnapshot.snapshot_date)).scalar()
    ticker_count = (
        db.query(func.count(func.distinct(FeatureSnapshot.ticker))).scalar() or 0
    )

    # Feature coverage: % of non-NULL feature columns (excluding targets + meta)
    # Sample last snapshot date for coverage calculation
    if last_date:
        sample = (
            db.query(FeatureSnapshot)
            .filter(FeatureSnapshot.snapshot_date == last_date)
            .limit(100)
            .all()
        )
        if sample:
            feature_cols = [
                "ark_in_etf_count", "ark_total_weight", "ark_conviction_score",
                "insider_net_buy_count_30d", "insider_cluster_active",
                "analyst_rating_score", "analyst_upgrades_30d",
                "pe_ratio", "forward_pe", "price_vs_sma50", "rsi_14",
                "earnings_days_until",
            ]
            filled = 0
            checks = 0
            for row in sample:
                for col in feature_cols:
                    checks += 1
                    if getattr(row, col, None) is not None:
                        filled += 1
            coverage = round(filled / checks * 100, 1) if checks else 0.0
        else:
            coverage = 0.0
    else:
        coverage = 0.0

    # Target backfill: % of rows where at least return_1d is filled
    filled_targets = (
        db.query(func.count())
        .select_from(FeatureSnapshot)
        .filter(FeatureSnapshot.return_1d.isnot(None))
        .scalar() or 0
    )
    backfill_pct = round(filled_targets / total * 100, 1) if total else 0.0

    return FeatureStats(
        last_snapshot_date=last_date,
        ticker_count=ticker_count,
        feature_coverage_pct=coverage,
        target_backfill_pct=backfill_pct,
        total_snapshots=total,
    )


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

            # Check if currently running
            running = job_tracker.is_running(job.id)

            collectors.append(
                CollectorStatus(
                    id=job.id,
                    name=job.name,
                    last_run=last_log.started_at if last_log else None,
                    last_status="running" if running else (
                        last_log.status if last_log else None
                    ),
                    records_written=last_log.records_written if last_log else None,
                    next_run=job.next_run_time,
                    is_running=running,
                )
            )

    # ── Table Statistics ─────────────────────────────────────────────
    table_stats = []
    for table_name, model, date_col_name, date_range_col in _TABLE_MODELS:
        try:
            # Universe: count only active tickers
            if model is Universe:
                row_count = (
                    db.query(func.count())
                    .select_from(model)
                    .filter(Universe.is_active.is_(True))
                    .scalar() or 0
                )
            else:
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
    db_connected = False
    alembic_rev = None
    try:
        db.execute(text("SELECT 1"))
        db_connected = True
    except Exception:
        pass

    if db_connected:
        try:
            result = db.execute(
                text("SELECT version_num FROM signals.alembic_version LIMIT 1")
            ).first()
            if result:
                alembic_rev = result[0]
        except Exception:
            pass  # Table may not exist yet

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
