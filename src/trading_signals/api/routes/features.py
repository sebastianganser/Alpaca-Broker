"""Features API routes.

Provides feature pipeline exploration: coverage matrix, signal
convergence, return statistics, and per-ticker feature details.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, case, select
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db
from trading_signals.api.schemas import (
    FeatureCoverageItem,
    FeatureCoverageResponse,
    FeatureGroupDetail,
    HorizonStats,
    ReturnStatsResponse,
    SignalConvergenceItem,
    SignalConvergenceResponse,
    TickerFeatureDetail,
)
from trading_signals.db.models.features import FeatureSnapshot

router = APIRouter(prefix="/features")

# ── Feature group column definitions ─────────────────────────────────

FEATURE_GROUPS = {
    "ARK": [
        "ark_in_etf_count", "ark_total_weight", "ark_weight_delta_1d",
        "ark_weight_delta_5d", "ark_weight_delta_20d", "ark_conviction_score",
        "ark_multi_etf_signal", "ark_increase_days_10d", "ark_increase_days_20d",
        "ark_conviction_streak", "ark_weight_trend_20d",
    ],
    "Insider": [
        "insider_net_buy_count_30d", "insider_buy_value_30d",
        "insider_cluster_active", "insider_cluster_score",
        "cluster_count_30d", "cluster_count_60d",
        "cluster_score_sum_60d", "days_since_last_cluster",
    ],
    "Analyst": [
        "analyst_rating_score", "analyst_upgrades_30d",
        "analyst_price_target_upside", "analyst_downgrades_30d",
        "analyst_net_sentiment_30d", "analyst_net_sentiment_60d",
        "analyst_upgrade_streak",
    ],
    "Politician": [
        "politician_buy_count_60d_disclosure",
        "politician_distinct_90d_disclosure",
        "politician_buy_count_60d_transaction",
        "politician_distinct_90d_transaction",
    ],
    "13F": [
        "form13f_top_holder_count", "form13f_new_positions_count",
    ],
    "Fundamentals": [
        "pe_ratio", "forward_pe", "ps_ratio", "revenue_growth_yoy",
        "profit_margin", "debt_to_equity", "pe_trend_4w", "margin_trend_4w",
    ],
    "Technical": [
        "price_vs_sma50", "price_vs_sma200", "rsi_14",
        "relative_strength_spy", "volume_ratio_20d", "atr_14_pct",
    ],
    "Earnings": [
        "earnings_days_until", "consecutive_beats", "surprise_trend_3q",
    ],
}

# All feature column names (flat list)
ALL_FEATURE_COLS = [col for cols in FEATURE_GROUPS.values() for col in cols]

# Source detection: which columns indicate an active source?
SOURCE_INDICATORS = {
    "ARK": "ark_in_etf_count",
    "Insider": "insider_net_buy_count_30d",
    "Analyst": "analyst_rating_score",
    "Politician": "politician_buy_count_60d_disclosure",
    "13F": "form13f_top_holder_count",
    "Fundamentals": "pe_ratio",
    "Technical": "rsi_14",
    "Earnings": "earnings_days_until",
}


def _get_latest_date(db: Session):
    """Get the most recent snapshot date."""
    return db.query(func.max(FeatureSnapshot.snapshot_date)).scalar()


def _count_filled(row: FeatureSnapshot, columns: list[str]) -> int:
    """Count non-NULL columns for a row."""
    return sum(1 for col in columns if getattr(row, col, None) is not None)


# ── GET /features/coverage ───────────────────────────────────────────

@router.get("/coverage", response_model=FeatureCoverageResponse)
def get_feature_coverage(db: Session = Depends(get_db)):
    """Feature coverage matrix: how many features are filled per ticker per group.

    Returns data for the latest snapshot date only.
    """
    latest = _get_latest_date(db)
    if not latest:
        return FeatureCoverageResponse()

    rows = (
        db.query(FeatureSnapshot)
        .filter(FeatureSnapshot.snapshot_date == latest)
        .order_by(FeatureSnapshot.ticker)
        .all()
    )

    items = []
    for row in rows:
        ark = _count_filled(row, FEATURE_GROUPS["ARK"])
        insider = _count_filled(row, FEATURE_GROUPS["Insider"])
        analyst = _count_filled(row, FEATURE_GROUPS["Analyst"])
        politician = _count_filled(row, FEATURE_GROUPS["Politician"])
        form13f = _count_filled(row, FEATURE_GROUPS["13F"])
        fundamentals = _count_filled(row, FEATURE_GROUPS["Fundamentals"])
        technical = _count_filled(row, FEATURE_GROUPS["Technical"])
        earnings = _count_filled(row, FEATURE_GROUPS["Earnings"])
        total = ark + insider + analyst + politician + form13f + fundamentals + technical + earnings

        items.append(FeatureCoverageItem(
            ticker=row.ticker,
            ark=ark,
            insider=insider,
            analyst=analyst,
            politician=politician,
            form13f=form13f,
            fundamentals=fundamentals,
            technical=technical,
            earnings=earnings,
            total_filled=total,
        ))

    return FeatureCoverageResponse(
        snapshot_date=latest,
        items=items,
        ticker_count=len(items),
    )


# ── GET /features/convergence ────────────────────────────────────────

@router.get("/convergence", response_model=SignalConvergenceResponse)
def get_signal_convergence(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Top tickers by number of active signal sources.

    A source is "active" if its primary indicator column is non-NULL
    and has a meaningful value (>0 for counts, not None for scores).
    """
    latest = _get_latest_date(db)
    if not latest:
        return SignalConvergenceResponse()

    rows = (
        db.query(FeatureSnapshot)
        .filter(FeatureSnapshot.snapshot_date == latest)
        .all()
    )

    items = []
    for row in rows:
        sources = []
        for source_name, col_name in SOURCE_INDICATORS.items():
            val = getattr(row, col_name, None)
            if val is not None:
                # For counts, only count as active if > 0
                if isinstance(val, (int, float)) and source_name in (
                    "ARK", "Insider", "Politician", "13F"
                ):
                    if val > 0:
                        sources.append(source_name)
                else:
                    sources.append(source_name)

        if sources:
            items.append(SignalConvergenceItem(
                ticker=row.ticker,
                active_sources=len(sources),
                source_names=sources,
                ark_conviction_score=(
                    float(row.ark_conviction_score)
                    if row.ark_conviction_score is not None else None
                ),
                insider_cluster_score=(
                    float(row.insider_cluster_score)
                    if row.insider_cluster_score is not None else None
                ),
                analyst_rating_score=(
                    float(row.analyst_rating_score)
                    if row.analyst_rating_score is not None else None
                ),
                rsi_14=(
                    float(row.rsi_14)
                    if row.rsi_14 is not None else None
                ),
            ))

    # Sort by most active sources, then alphabetically
    items.sort(key=lambda x: (-x.active_sources, x.ticker))

    return SignalConvergenceResponse(
        snapshot_date=latest,
        items=items[:limit],
    )


# ── GET /features/returns ────────────────────────────────────────────

@router.get("/returns", response_model=ReturnStatsResponse)
def get_return_stats(db: Session = Depends(get_db)):
    """Aggregated forward return statistics across all snapshots."""
    total = db.query(func.count()).select_from(FeatureSnapshot).scalar() or 0
    if total == 0:
        return ReturnStatsResponse()

    horizons = []
    for col_name, label in [
        ("return_1d", "1d"),
        ("return_5d", "5d"),
        ("return_20d", "20d"),
        ("return_60d", "60d"),
    ]:
        col = getattr(FeatureSnapshot, col_name)
        stats = db.query(
            func.count(col).label("filled"),
            func.avg(col).label("mean"),
            func.stddev(col).label("std"),
            func.min(col).label("min_val"),
            func.max(col).label("max_val"),
        ).first()

        filled = stats.filled or 0

        # Median via percentile_cont (Postgres-specific)
        median = None
        if filled > 0:
            try:
                median_result = db.execute(
                    select(
                        func.percentile_cont(0.5)
                        .within_group(col)
                    )
                ).scalar()
                median = round(float(median_result), 6) if median_result else None
            except Exception:
                median = None

        horizons.append(HorizonStats(
            horizon=label,
            filled_count=filled,
            total_count=total,
            filled_pct=round(filled / total * 100, 1) if total else 0.0,
            mean=round(float(stats.mean), 6) if stats.mean is not None else None,
            median=median,
            std=round(float(stats.std), 6) if stats.std is not None else None,
            min_val=round(float(stats.min_val), 6) if stats.min_val is not None else None,
            max_val=round(float(stats.max_val), 6) if stats.max_val is not None else None,
        ))

    return ReturnStatsResponse(horizons=horizons, total_snapshots=total)


# ── GET /features/ticker/{symbol} ────────────────────────────────────

@router.get("/ticker/{symbol}", response_model=TickerFeatureDetail)
def get_ticker_features(symbol: str, db: Session = Depends(get_db)):
    """All feature values for a ticker's latest snapshot."""
    row = (
        db.query(FeatureSnapshot)
        .filter(FeatureSnapshot.ticker == symbol.upper())
        .order_by(FeatureSnapshot.snapshot_date.desc())
        .first()
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No feature snapshot found for '{symbol.upper()}'",
        )

    groups = []
    total_filled = 0
    for group_name, cols in FEATURE_GROUPS.items():
        features = {}
        filled = 0
        for col in cols:
            val = getattr(row, col, None)
            if val is not None:
                filled += 1
                # Convert Decimal to float for JSON serialization
                if hasattr(val, '__float__'):
                    val = round(float(val), 6)
            features[col] = val

        total_filled += filled
        groups.append(FeatureGroupDetail(
            group=group_name,
            features=features,
            filled=filled,
            total=len(cols),
        ))

    return TickerFeatureDetail(
        ticker=row.ticker,
        snapshot_date=row.snapshot_date,
        groups=groups,
        total_filled=total_filled,
        return_1d=float(row.return_1d) if row.return_1d is not None else None,
        return_5d=float(row.return_5d) if row.return_5d is not None else None,
        return_20d=float(row.return_20d) if row.return_20d is not None else None,
        return_60d=float(row.return_60d) if row.return_60d is not None else None,
    )
