"""Signals API routes.

Provides recent signal data: ARK deltas, insider clusters,
politician trades, and analyst ratings.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db
from trading_signals.api.schemas import (
    AnalystRatingItem,
    ARKDeltaItem,
    InsiderClusterItem,
    PoliticianTradeItem,
)
from trading_signals.db.models import (
    AnalystRating,
    ARKDelta,
    InsiderCluster,
    PoliticianTrade,
)

router = APIRouter(prefix="/signals")


@router.get("/ark", response_model=list[ARKDeltaItem])
def get_ark_deltas(
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="Lookback days"),
    limit: int = Query(100, ge=1, le=500),
):
    """Get recent ARK ETF delta movements.

    Shows new positions, closed positions, and significant weight changes.
    """
    cutoff = date.today() - timedelta(days=days)
    deltas = (
        db.query(ARKDelta)
        .filter(
            ARKDelta.delta_date >= cutoff,
            ARKDelta.delta_type != "unchanged",
        )
        .order_by(desc(ARKDelta.delta_date), ARKDelta.ticker)
        .limit(limit)
        .all()
    )

    return [
        ARKDeltaItem(
            delta_date=d.delta_date,
            etf_ticker=d.etf_ticker,
            ticker=d.ticker,
            delta_type=d.delta_type,
            shares_delta=float(d.shares_delta) if d.shares_delta else None,
            shares_prev=float(d.shares_prev) if d.shares_prev else None,
            shares_curr=float(d.shares_curr) if d.shares_curr else None,
            weight_delta=float(d.weight_delta) if d.weight_delta else None,
            weight_prev=float(d.weight_prev) if d.weight_prev else None,
            weight_curr=float(d.weight_curr) if d.weight_curr else None,
        )
        for d in deltas
    ]


@router.get("/insider", response_model=list[InsiderClusterItem])
def get_insider_clusters(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365, description="Lookback days"),
    min_score: float = Query(0.0, ge=0.0, description="Minimum cluster score"),
    limit: int = Query(50, ge=1, le=200),
):
    """Get active insider trading clusters.

    Returns clusters where multiple insiders traded the same stock
    within a short time window.
    """
    cutoff = date.today() - timedelta(days=days)
    clusters = (
        db.query(InsiderCluster)
        .filter(InsiderCluster.cluster_end >= cutoff)
        .filter(InsiderCluster.cluster_score >= min_score)
        .order_by(desc(InsiderCluster.cluster_score))
        .limit(limit)
        .all()
    )

    return [
        InsiderClusterItem(
            ticker=c.ticker,
            cluster_start=c.cluster_start,
            cluster_end=c.cluster_end,
            n_insiders=c.n_insiders,
            n_buys=c.n_buys,
            n_sells=c.n_sells,
            total_buy_value=float(c.total_buy_value) if c.total_buy_value else None,
            cluster_score=float(c.cluster_score) if c.cluster_score else None,
        )
        for c in clusters
    ]


@router.get("/politicians", response_model=list[PoliticianTradeItem])
def get_politician_trades(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365, description="Lookback days"),
    limit: int = Query(100, ge=1, le=500),
):
    """Get recent politician trades from Senate financial disclosures."""
    cutoff = date.today() - timedelta(days=days)
    trades = (
        db.query(PoliticianTrade)
        .filter(PoliticianTrade.disclosure_date >= cutoff)
        .order_by(desc(PoliticianTrade.disclosure_date))
        .limit(limit)
        .all()
    )

    return [
        PoliticianTradeItem(
            politician_name=t.politician_name,
            party=t.party,
            ticker=t.ticker,
            transaction_date=t.transaction_date,
            disclosure_date=t.disclosure_date,
            transaction_type=t.transaction_type,
            amount_range=t.amount_range,
        )
        for t in trades
    ]


@router.get("/ratings", response_model=list[AnalystRatingItem])
def get_analyst_ratings(
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="Lookback days"),
    limit: int = Query(100, ge=1, le=500),
):
    """Get recent analyst rating changes (upgrades/downgrades)."""
    cutoff = date.today() - timedelta(days=days)
    ratings = (
        db.query(AnalystRating)
        .filter(AnalystRating.rating_date >= cutoff)
        .order_by(desc(AnalystRating.rating_date))
        .limit(limit)
        .all()
    )

    return [
        AnalystRatingItem(
            ticker=r.ticker,
            firm=r.firm,
            rating_date=r.rating_date,
            rating_new=r.rating_new,
            rating_old=r.rating_old,
            action=r.action,
            price_target_new=float(r.price_target_new) if r.price_target_new else None,
            price_target_old=float(r.price_target_old) if r.price_target_old else None,
        )
        for r in ratings
    ]
