"""Features API routes.

Provides feature pipeline exploration: coverage matrix, signal
convergence, return statistics, and per-ticker feature details.

Feature groups
--------------
Groups are derived from the ORM model, never hard-coded here:
``FEATURE_COLUMNS`` (``db/models/features.py``, key/meta/target columns
excluded) is grouped by
:func:`trading_signals.analysis.feature_groups.group_features`. New model
columns therefore show up automatically (worst case in the "Other" group)
and the API never reports stale per-group totals.

Convergence
-----------
A group counts as an active *source* for a ticker when its indicator
column is set (and > 0 for count-type indicators). Market-wide groups
(macro, breadth – every column identical for all tickers) are excluded
from the convergence count because they carry no ticker-specific signal.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_signals.analysis.feature_groups import (
    OTHER_GROUP,
    group_features,
    is_market_wide,
)
from trading_signals.api.deps import get_db
from trading_signals.api.schemas import (
    FeatureCoverageItem,
    FeatureCoverageResponse,
    FeatureGroupDetail,
    FeatureGroupMeta,
    HorizonStats,
    ReturnStatsResponse,
    SignalConvergenceItem,
    SignalConvergenceResponse,
    TickerFeatureDetail,
)
from trading_signals.db.models.features import FeatureSnapshot

router = APIRouter(prefix="/features")

# ── Feature group definitions (derived from the model) ───────────────

OTHER_GROUP_KEY = "other"

#: Stable API keys for the reporting groups of ``group_features``.
_GROUP_KEYS: dict[str, str] = {
    "13F": "form13f",
    OTHER_GROUP: OTHER_GROUP_KEY,
}

#: Convergence indicator per group: (column, must_be_positive).
#: A group without an entry here never counts as a signal source.
_SOURCE_INDICATOR_DEFS: dict[str, tuple[str, bool]] = {
    "ARK": ("ark_in_etf_count", True),
    "Insider": ("insider_net_buy_count_30d", True),
    "Analyst": ("analyst_rating_score", False),
    "Politician": ("politician_buy_count_60d_disclosure", True),
    "13F": ("form13f_top_holder_count", True),
    "Fundamentals": ("pe_ratio", False),
    "Technical": ("rsi_14", False),
    "Earnings": ("earnings_days_until", False),
    "Sentiment": ("sentiment_avg_7d", False),
    "Liquidity": ("dollar_volume_20d", False),
    "Macro": ("macro_vix", False),
    "Breadth": ("breadth_advance_decline", False),
    "Sector": ("sector_relative_return_20d", False),
    "Short Interest": ("short_volume_ratio_5d", False),
    "Options IV": ("options_iv_atm_30d", False),
    "Estimates": ("eps_revision_pct_30d", False),
}


@dataclass(frozen=True)
class FeatureGroupDef:
    """Definition of a feature group."""

    key: str                     # stable API key
    label: str                   # display label
    columns: tuple[str, ...]
    indicator: str | None = None  # column that marks the source as active
    positive_only: bool = False   # indicator must be > 0 (count features)
    market_wide: bool = False     # identical for all tickers → no "signal"


def group_key(label: str) -> str:
    """Stable machine key for a group label (e.g. "Short Interest" → short_interest)."""
    return _GROUP_KEYS.get(label, label.lower().replace(" ", "_"))


def build_feature_groups(
    features: Iterable[str] | None = None,
) -> list[FeatureGroupDef]:
    """Build API group definitions from the model's feature columns.

    Grouping rules live in :mod:`trading_signals.analysis.feature_groups`;
    columns default to ``FEATURE_COLUMNS`` (meta/target columns excluded).
    """
    groups: list[FeatureGroupDef] = []
    for label, cols in group_features(features).items():
        indicator, positive_only = _SOURCE_INDICATOR_DEFS.get(label, (None, False))
        if indicator not in cols:
            indicator, positive_only = None, False
        groups.append(FeatureGroupDef(
            key=group_key(label),
            label=label,
            columns=tuple(cols),
            indicator=indicator,
            positive_only=positive_only,
            market_wide=all(is_market_wide(c) for c in cols),
        ))
    return groups


FEATURE_GROUP_DEFS: list[FeatureGroupDef] = build_feature_groups()

# Convenience views (label → columns / indicator)
FEATURE_GROUPS: dict[str, list[str]] = {
    g.label: list(g.columns) for g in FEATURE_GROUP_DEFS
}
ALL_FEATURE_COLS = [col for g in FEATURE_GROUP_DEFS for col in g.columns]
SOURCE_INDICATORS: dict[str, str] = {
    g.label: g.indicator for g in FEATURE_GROUP_DEFS if g.indicator
}
#: Groups that can count as a ticker-specific signal source.
SIGNAL_GROUPS: list[FeatureGroupDef] = [
    g for g in FEATURE_GROUP_DEFS if g.indicator and not g.market_wide
]
MARKET_WIDE_GROUPS: list[FeatureGroupDef] = [
    g for g in FEATURE_GROUP_DEFS if g.market_wide
]


def group_meta() -> list[FeatureGroupMeta]:
    return [
        FeatureGroupMeta(
            key=g.key, label=g.label, total=len(g.columns), market_wide=g.market_wide,
        )
        for g in FEATURE_GROUP_DEFS
    ]


def _get_latest_date(db: Session):
    """Get the most recent snapshot date."""
    return db.query(func.max(FeatureSnapshot.snapshot_date)).scalar()


def _count_filled(row: FeatureSnapshot, columns: list[str] | tuple[str, ...]) -> int:
    """Count non-NULL columns for a row."""
    return sum(1 for col in columns if getattr(row, col, None) is not None)


def active_sources(row: object) -> list[str]:
    """Labels of ticker-specific signal groups that are active for ``row``."""
    sources: list[str] = []
    for g in SIGNAL_GROUPS:
        assert g.indicator is not None
        val = getattr(row, g.indicator, None)
        if val is None:
            continue
        if g.positive_only:
            try:
                if float(val) > 0:
                    sources.append(g.label)
            except (TypeError, ValueError):
                continue
        else:
            sources.append(g.label)
    return sources


def _opt_float(value) -> float | None:
    return float(value) if value is not None else None


def _round6(value) -> float | None:
    return round(float(value), 6) if value is not None else None


# ── GET /features/groups ─────────────────────────────────────────────

@router.get("/groups", response_model=list[FeatureGroupMeta])
def get_feature_groups():
    """Feature group metadata (key, label, column count, market-wide flag)."""
    return group_meta()


# ── GET /features/coverage ───────────────────────────────────────────

@router.get("/coverage", response_model=FeatureCoverageResponse)
def get_feature_coverage(db: Session = Depends(get_db)):
    """Feature coverage matrix: how many features are filled per ticker per group.

    Returns data for the latest snapshot date only.
    """
    meta = group_meta()
    total_possible = len(ALL_FEATURE_COLS)
    latest = _get_latest_date(db)
    if not latest:
        return FeatureCoverageResponse(groups=meta, total_possible=total_possible)

    rows = (
        db.query(FeatureSnapshot)
        .filter(FeatureSnapshot.snapshot_date == latest)
        .order_by(FeatureSnapshot.ticker)
        .all()
    )

    items = []
    for row in rows:
        counts = {g.key: _count_filled(row, g.columns) for g in FEATURE_GROUP_DEFS}
        items.append(FeatureCoverageItem(
            ticker=row.ticker,
            counts=counts,
            total_filled=sum(counts.values()),
            total_possible=total_possible,
        ))

    return FeatureCoverageResponse(
        snapshot_date=latest,
        groups=meta,
        total_possible=total_possible,
        items=items,
        ticker_count=len(items),
    )


# ── GET /features/convergence ────────────────────────────────────────

@router.get("/convergence", response_model=SignalConvergenceResponse)
def get_signal_convergence(
    limit: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Top tickers by number of active ticker-specific signal sources.

    A source is "active" if its primary indicator column is non-NULL
    and has a meaningful value (>0 for counts, not None for scores).
    Market-wide groups (macro, breadth) are excluded.
    """
    base = SignalConvergenceResponse(
        max_sources=len(SIGNAL_GROUPS),
        excluded_groups=[g.label for g in MARKET_WIDE_GROUPS],
    )
    latest = _get_latest_date(db)
    if not latest:
        return base

    rows = (
        db.query(FeatureSnapshot)
        .filter(FeatureSnapshot.snapshot_date == latest)
        .all()
    )

    items = []
    for row in rows:
        sources = active_sources(row)
        if sources:
            items.append(SignalConvergenceItem(
                ticker=row.ticker,
                active_sources=len(sources),
                source_names=sources,
                ark_conviction_score=_opt_float(row.ark_conviction_score),
                insider_cluster_score=_opt_float(row.insider_cluster_score),
                analyst_rating_score=_opt_float(row.analyst_rating_score),
                rsi_14=_opt_float(row.rsi_14),
                sentiment_avg_7d=_opt_float(row.sentiment_avg_7d),
            ))

    # Sort by most active sources, then alphabetically
    items.sort(key=lambda x: (-x.active_sources, x.ticker))

    return base.model_copy(update={
        "snapshot_date": latest,
        "total": len(items),
        "items": items[:limit],
    })


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
                median = _round6(median_result)
            except Exception:
                median = None

        horizons.append(HorizonStats(
            horizon=label,
            filled_count=filled,
            total_count=total,
            filled_pct=round(filled / total * 100, 1) if total else 0.0,
            mean=_round6(stats.mean),
            median=median,
            std=_round6(stats.std),
            min_val=_round6(stats.min_val),
            max_val=_round6(stats.max_val),
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
    for g in FEATURE_GROUP_DEFS:
        features = {}
        filled = 0
        for col in g.columns:
            val = getattr(row, col, None)
            if val is not None:
                filled += 1
                # Convert Decimal to float for JSON serialization (keep bool/int)
                if not isinstance(val, (bool, int)) and hasattr(val, "__float__"):
                    val = round(float(val), 6)
            features[col] = val

        total_filled += filled
        groups.append(FeatureGroupDetail(
            group=g.label,
            key=g.key,
            market_wide=g.market_wide,
            features=features,
            filled=filled,
            total=len(g.columns),
        ))

    return TickerFeatureDetail(
        ticker=row.ticker,
        snapshot_date=row.snapshot_date,
        groups=groups,
        total_filled=total_filled,
        total_possible=len(ALL_FEATURE_COLS),
        return_1d=_opt_float(row.return_1d),
        return_5d=_opt_float(row.return_5d),
        return_20d=_opt_float(row.return_20d),
        return_60d=_opt_float(row.return_60d),
    )
