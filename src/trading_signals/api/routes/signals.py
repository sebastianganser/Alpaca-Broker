"""Signals API routes.

Provides recent signal data: ARK deltas, insider clusters,
politician trades, and analyst ratings.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db
from trading_signals.api.schemas import (
    AnalystRatingItem,
    ARKDeltaItem,
    ARKSummaryItem,
    InsiderClusterItem,
    PoliticianTradeItem,
)
from trading_signals.db.models import (
    AnalystRating,
    ARKDelta,
    InsiderCluster,
    PoliticianTrade,
)
from trading_signals.db.models.fundamentals import FundamentalsSnapshot

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


@router.get("/ark/summary", response_model=list[ARKSummaryItem])
def get_ark_summary(
    db: Session = Depends(get_db),
    days: int = Query(5, ge=1, le=90, description="Lookback window in days"),
):
    """Get aggregated ARK moves per ticker across all ETFs.

    Groups ARK delta entries by ticker over the given time window,
    summing shares and weight changes across all ETFs and days.
    Sorted by absolute weight impact (strongest moves first).
    """
    cutoff = date.today() - timedelta(days=days)
    deltas = (
        db.query(ARKDelta)
        .filter(
            ARKDelta.delta_date >= cutoff,
            ARKDelta.delta_type != "unchanged",
        )
        .all()
    )

    # Group by ticker
    ticker_data: dict[str, list] = {}
    for d in deltas:
        ticker_data.setdefault(d.ticker, []).append(d)

    results = []
    for ticker, entries in ticker_data.items():
        total_shares = sum(float(e.shares_delta or 0) for e in entries)
        total_weight = sum(float(e.weight_delta or 0) for e in entries)
        etfs = sorted(set(e.etf_ticker for e in entries))
        dates = sorted(set(e.delta_date for e in entries))

        if total_shares > 0:
            direction = "increased"
        elif total_shares < 0:
            direction = "decreased"
        else:
            direction = "mixed"

        results.append(
            ARKSummaryItem(
                ticker=ticker,
                total_shares_delta=total_shares,
                total_weight_delta_bps=total_weight * 100,  # Convert to bps
                n_etfs=len(etfs),
                n_days=len(dates),
                etfs=etfs,
                direction=direction,
                first_date=dates[0],
                last_date=dates[-1],
            )
        )

    # Sort by absolute weight impact (strongest moves first)
    results.sort(key=lambda x: abs(x.total_weight_delta_bps), reverse=True)
    return results


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
            delay_days=(
                (t.disclosure_date - t.transaction_date).days
                if t.disclosure_date and t.transaction_date
                else None
            ),
        )
        for t in trades
    ]


@router.get("/ratings", response_model=list[AnalystRatingItem])
def get_analyst_ratings(
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="Lookback days"),
    limit: int = Query(100, ge=1, le=500),
):
    """Get recent analyst rating changes (upgrades/downgrades).

    Enriches each rating with the consensus median price target from
    the latest fundamentals snapshot for that ticker.
    """
    cutoff = date.today() - timedelta(days=days)
    ratings = (
        db.query(AnalystRating)
        .filter(AnalystRating.rating_date >= cutoff)
        .order_by(desc(AnalystRating.rating_date))
        .limit(limit)
        .all()
    )

    # Build a lookup of consensus price targets from the latest snapshot
    # Subquery: max snapshot_date per ticker
    latest_date_sq = (
        db.query(
            FundamentalsSnapshot.ticker,
            func.max(FundamentalsSnapshot.snapshot_date).label("max_date"),
        )
        .group_by(FundamentalsSnapshot.ticker)
        .subquery()
    )
    targets = (
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
    target_map = {t.ticker: float(t.target_price_median) for t in targets}

    return [
        AnalystRatingItem(
            ticker=r.ticker,
            firm=r.firm,
            rating_date=r.rating_date,
            rating_new=r.rating_new,
            rating_old=r.rating_old,
            action=r.action,
            price_target_new=target_map.get(r.ticker),
            price_target_old=None,
        )
        for r in ratings
    ]
