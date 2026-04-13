"""Ticker detail API routes.

Provides per-ticker data: prices, indicators, fundamentals,
and all signals for a specific ticker.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db
from trading_signals.api.schemas import (
    AnalystRatingItem,
    ARKDeltaItem,
    FundamentalsData,
    IndicatorPoint,
    InsiderClusterItem,
    PoliticianTradeItem,
    PricePoint,
)
from trading_signals.db.models import (
    AnalystRating,
    ARKDelta,
    FundamentalsSnapshot,
    InsiderCluster,
    PoliticianTrade,
    PriceDaily,
    TechnicalIndicator,
    Universe,
)

router = APIRouter(prefix="/ticker")


def _validate_ticker(ticker: str, db: Session) -> str:
    """Check that ticker exists in universe, return uppercase symbol."""
    symbol = ticker.upper()
    exists = db.query(Universe.ticker).filter(Universe.ticker == symbol).first()
    if not exists:
        raise HTTPException(status_code=404, detail=f"Ticker {symbol} not found")
    return symbol


@router.get("/{symbol}/prices", response_model=list[PricePoint])
def get_prices(
    symbol: str,
    db: Session = Depends(get_db),
    period: str = Query("3m", description="Period: 1m, 3m, 6m, 1y, 5y, all"),
):
    """Get OHLCV price data for a ticker."""
    ticker = _validate_ticker(symbol, db)

    # Calculate cutoff date based on period
    period_map = {
        "1m": 30, "3m": 90, "6m": 180,
        "1y": 365, "5y": 1825, "all": 9999,
    }
    days = period_map.get(period, 90)
    cutoff = date.today() - timedelta(days=days)

    prices = (
        db.query(PriceDaily)
        .filter(PriceDaily.ticker == ticker, PriceDaily.trade_date >= cutoff)
        .order_by(PriceDaily.trade_date)
        .all()
    )

    return [
        PricePoint(
            trade_date=p.trade_date,
            open=float(p.open) if p.open else None,
            high=float(p.high) if p.high else None,
            low=float(p.low) if p.low else None,
            close=float(p.close) if p.close else None,
            volume=int(p.volume) if p.volume else None,
        )
        for p in prices
    ]


@router.get("/{symbol}/indicators", response_model=list[IndicatorPoint])
def get_indicators(
    symbol: str,
    db: Session = Depends(get_db),
    period: str = Query("3m", description="Period: 1m, 3m, 6m, 1y, all"),
):
    """Get technical indicators for a ticker."""
    ticker = _validate_ticker(symbol, db)

    period_map = {
        "1m": 30, "3m": 90, "6m": 180,
        "1y": 365, "all": 9999,
    }
    days = period_map.get(period, 90)
    cutoff = date.today() - timedelta(days=days)

    indicators = (
        db.query(TechnicalIndicator)
        .filter(
            TechnicalIndicator.ticker == ticker,
            TechnicalIndicator.trade_date >= cutoff,
        )
        .order_by(TechnicalIndicator.trade_date)
        .all()
    )

    return [
        IndicatorPoint(
            trade_date=i.trade_date,
            sma_20=float(i.sma_20) if i.sma_20 else None,
            sma_50=float(i.sma_50) if i.sma_50 else None,
            sma_200=float(i.sma_200) if i.sma_200 else None,
            ema_12=float(i.ema_12) if i.ema_12 else None,
            ema_26=float(i.ema_26) if i.ema_26 else None,
            rsi_14=float(i.rsi_14) if i.rsi_14 else None,
            macd=float(i.macd) if i.macd else None,
            macd_signal=float(i.macd_signal) if i.macd_signal else None,
            macd_histogram=float(i.macd_histogram) if i.macd_histogram else None,
            bollinger_upper=float(i.bollinger_upper) if i.bollinger_upper else None,
            bollinger_lower=float(i.bollinger_lower) if i.bollinger_lower else None,
            atr_14=float(i.atr_14) if i.atr_14 else None,
            volume_sma_20=float(i.volume_sma_20) if i.volume_sma_20 else None,
            relative_strength_spy=(
                float(i.relative_strength_spy) if i.relative_strength_spy else None
            ),
        )
        for i in indicators
    ]


@router.get("/{symbol}/fundamentals", response_model=FundamentalsData | None)
def get_fundamentals(
    symbol: str,
    db: Session = Depends(get_db),
):
    """Get the latest fundamentals snapshot for a ticker."""
    ticker = _validate_ticker(symbol, db)

    f = (
        db.query(FundamentalsSnapshot)
        .filter(FundamentalsSnapshot.ticker == ticker)
        .order_by(desc(FundamentalsSnapshot.snapshot_date))
        .first()
    )

    if not f:
        return None

    return FundamentalsData(
        snapshot_date=f.snapshot_date,
        market_cap=float(f.market_cap) if f.market_cap else None,
        pe_ratio=float(f.pe_ratio) if f.pe_ratio else None,
        forward_pe=float(f.forward_pe) if f.forward_pe else None,
        ps_ratio=float(f.ps_ratio) if f.ps_ratio else None,
        pb_ratio=float(f.pb_ratio) if f.pb_ratio else None,
        ev_ebitda=float(f.ev_ebitda) if f.ev_ebitda else None,
        profit_margin=float(f.profit_margin) if f.profit_margin else None,
        operating_margin=float(f.operating_margin) if f.operating_margin else None,
        return_on_equity=float(f.return_on_equity) if f.return_on_equity else None,
        revenue_growth_yoy=(
            float(f.revenue_growth_yoy) if f.revenue_growth_yoy else None
        ),
        eps_ttm=float(f.eps_ttm) if f.eps_ttm else None,
        debt_to_equity=float(f.debt_to_equity) if f.debt_to_equity else None,
        dividend_yield=float(f.dividend_yield) if f.dividend_yield else None,
        beta=float(f.beta) if f.beta else None,
    )


@router.get("/{symbol}/signals")
def get_ticker_signals(
    symbol: str,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
):
    """Get all signal data for a specific ticker."""
    ticker = _validate_ticker(symbol, db)
    cutoff = date.today() - timedelta(days=days)

    # ARK Deltas
    ark_deltas = (
        db.query(ARKDelta)
        .filter(ARKDelta.ticker == ticker, ARKDelta.delta_date >= cutoff)
        .order_by(desc(ARKDelta.delta_date))
        .limit(20)
        .all()
    )

    # Insider Clusters
    clusters = (
        db.query(InsiderCluster)
        .filter(InsiderCluster.ticker == ticker, InsiderCluster.cluster_end >= cutoff)
        .order_by(desc(InsiderCluster.cluster_score))
        .limit(10)
        .all()
    )

    # Politician Trades
    pol_trades = (
        db.query(PoliticianTrade)
        .filter(
            PoliticianTrade.ticker == ticker,
            PoliticianTrade.disclosure_date >= cutoff,
        )
        .order_by(desc(PoliticianTrade.disclosure_date))
        .limit(20)
        .all()
    )

    # Analyst Ratings
    ratings = (
        db.query(AnalystRating)
        .filter(AnalystRating.ticker == ticker, AnalystRating.rating_date >= cutoff)
        .order_by(desc(AnalystRating.rating_date))
        .limit(20)
        .all()
    )

    return {
        "ark_deltas": [
            ARKDeltaItem(
                delta_date=d.delta_date,
                etf_ticker=d.etf_ticker,
                ticker=d.ticker,
                shares_delta=float(d.shares_delta) if d.shares_delta else None,
                weight_delta_bps=(
                    float(d.weight_delta_bps) if d.weight_delta_bps else None
                ),
                pct_change=float(d.pct_change) if d.pct_change else None,
                is_new_position=d.is_new_position or False,
                is_closed_position=d.is_closed_position or False,
            )
            for d in ark_deltas
        ],
        "insider_clusters": [
            InsiderClusterItem(
                ticker=c.ticker,
                cluster_start=c.cluster_start,
                cluster_end=c.cluster_end,
                n_insiders=c.n_insiders,
                n_buys=c.n_buys,
                n_sells=c.n_sells,
                total_buy_value=(
                    float(c.total_buy_value) if c.total_buy_value else None
                ),
                cluster_score=float(c.cluster_score) if c.cluster_score else None,
            )
            for c in clusters
        ],
        "politician_trades": [
            PoliticianTradeItem(
                politician_name=t.politician_name,
                party=t.party,
                ticker=t.ticker,
                transaction_date=t.transaction_date,
                disclosure_date=t.disclosure_date,
                transaction_type=t.transaction_type,
                amount_range=t.amount_range,
            )
            for t in pol_trades
        ],
        "analyst_ratings": [
            AnalystRatingItem(
                ticker=r.ticker,
                firm=r.firm,
                rating_date=r.rating_date,
                rating_new=r.rating_new,
                rating_old=r.rating_old,
                action=r.action,
                price_target_new=(
                    float(r.price_target_new) if r.price_target_new else None
                ),
                price_target_old=(
                    float(r.price_target_old) if r.price_target_old else None
                ),
            )
            for r in ratings
        ],
    }
