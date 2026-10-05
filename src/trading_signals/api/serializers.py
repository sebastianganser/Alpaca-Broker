"""Shared ORM → API schema serializers for signal data.

Used by both ``/signals/*`` and ``/ticker/{symbol}/signals`` so the two
endpoints always return identical shapes and semantics (e.g. the
difference between the *consensus* price target and a *firm's* target).

Numeric conversion rule: ``None`` stays ``None``, every other value –
including ``0`` – is converted. Never use ``x if x else None`` here, it
silently turns legitimate zeros into "missing".
"""

from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from trading_signals.api.schemas import (
    AnalystRatingItem,
    ARKDeltaItem,
    InsiderClusterItem,
    PoliticianTradeItem,
)
from trading_signals.db.models import (
    AnalystRating,
    ARKDelta,
    FundamentalsSnapshot,
    InsiderCluster,
    PoliticianTrade,
)


def to_float(value: Decimal | float | int | None) -> float | None:
    """Convert a numeric DB value to float, preserving ``None`` (and 0)."""
    return float(value) if value is not None else None


def to_int(value: Decimal | float | int | None) -> int | None:
    """Convert a numeric DB value to int, preserving ``None`` (and 0)."""
    return int(value) if value is not None else None


def ark_delta_item(d: ARKDelta) -> ARKDeltaItem:
    return ARKDeltaItem(
        delta_date=d.delta_date,
        etf_ticker=d.etf_ticker,
        ticker=d.ticker,
        delta_type=d.delta_type,
        shares_delta=to_float(d.shares_delta),
        shares_prev=to_float(d.shares_prev),
        shares_curr=to_float(d.shares_curr),
        weight_delta=to_float(d.weight_delta),
        weight_prev=to_float(d.weight_prev),
        weight_curr=to_float(d.weight_curr),
    )


def insider_cluster_item(c: InsiderCluster) -> InsiderClusterItem:
    return InsiderClusterItem(
        ticker=c.ticker,
        cluster_start=c.cluster_start,
        cluster_end=c.cluster_end,
        n_insiders=c.n_insiders,
        n_buys=c.n_buys,
        n_sells=c.n_sells,
        total_buy_value=to_float(c.total_buy_value),
        cluster_score=to_float(c.cluster_score),
    )


def politician_trade_item(t: PoliticianTrade) -> PoliticianTradeItem:
    return PoliticianTradeItem(
        politician_name=t.politician_name,
        party=t.party,
        ticker=t.ticker,
        transaction_date=t.transaction_date,
        disclosure_date=t.disclosure_date,
        transaction_type=t.transaction_type,
        amount_range=t.amount_range,
        delay_days=(
            (t.disclosure_date - t.transaction_date).days
            if t.disclosure_date and t.transaction_date
            else None
        ),
    )


def analyst_rating_item(
    r: AnalystRating, consensus_targets: dict[str, float]
) -> AnalystRatingItem:
    """Serialize a rating change.

    ``firm_target_*`` is the target published by *this* firm with the
    rating change; ``consensus_target`` is the median target across all
    analysts from the latest fundamentals snapshot (context only).
    """
    return AnalystRatingItem(
        ticker=r.ticker,
        firm=r.firm,
        rating_date=r.rating_date,
        rating_new=r.rating_new,
        rating_old=r.rating_old,
        action=r.action,
        firm_target_new=to_float(r.price_target_new),
        firm_target_old=to_float(r.price_target_old),
        consensus_target=consensus_targets.get(r.ticker),
    )


def consensus_targets(db: Session, tickers: Iterable[str]) -> dict[str, float]:
    """Latest consensus median price target per ticker (only for ``tickers``)."""
    symbols = sorted(set(tickers))
    if not symbols:
        return {}

    latest_date_sq = (
        db.query(
            FundamentalsSnapshot.ticker,
            func.max(FundamentalsSnapshot.snapshot_date).label("max_date"),
        )
        .filter(FundamentalsSnapshot.ticker.in_(symbols))
        .group_by(FundamentalsSnapshot.ticker)
        .subquery()
    )
    rows = (
        db.query(
            FundamentalsSnapshot.ticker,
            FundamentalsSnapshot.target_price_median,
        )
        .join(
            latest_date_sq,
            (FundamentalsSnapshot.ticker == latest_date_sq.c.ticker)
            & (FundamentalsSnapshot.snapshot_date == latest_date_sq.c.max_date),
        )
        .filter(FundamentalsSnapshot.target_price_median.isnot(None))
        .all()
    )
    return {row.ticker: float(row.target_price_median) for row in rows}
