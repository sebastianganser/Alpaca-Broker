"""Ticker detail API routes.

Provides per-ticker data: prices, indicators, fundamentals,
and all signals for a specific ticker.
"""

from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db, get_scheduler
from trading_signals.api.schemas import (
    DataQualityDimension,
    FundamentalsData,
    IndicatorPoint,
    PricePoint,
    TickerDataQuality,
    TickerSignalCounts,
    TickerSignals,
)
from trading_signals.api.serializers import (
    analyst_rating_item,
    ark_delta_item,
    consensus_targets,
    insider_cluster_item,
    politician_trade_item,
    to_float,
    to_int,
)
from trading_signals.db.models import (
    AnalystRating,
    ARKDelta,
    CollectionLog,
    FundamentalsSnapshot,
    InsiderCluster,
    PoliticianTrade,
    PriceDaily,
    TechnicalIndicator,
    Universe,
)
from trading_signals.scheduler.registry import get_collector_name
from trading_signals.utils import job_status

router = APIRouter(prefix="/ticker")

Period = Literal["1m", "3m", "6m", "1y", "5y", "all"]

PERIOD_DAYS: dict[str, int] = {
    "1m": 30, "3m": 90, "6m": 180,
    "1y": 365, "5y": 1825, "all": 9999,
}

#: Scheduler job id and collection_log.collector_name of the price collector.
PRICE_JOB_ID = "price_collector"
PRICE_COLLECTOR_NAME = get_collector_name(PRICE_JOB_ID)


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
    period: Period = Query("3m", description="Period: 1m, 3m, 6m, 1y, 5y, all"),
):
    """Get OHLCV price data for a ticker."""
    ticker = _validate_ticker(symbol, db)
    cutoff = date.today() - timedelta(days=PERIOD_DAYS[period])

    prices = (
        db.query(PriceDaily)
        .filter(PriceDaily.ticker == ticker, PriceDaily.trade_date >= cutoff)
        .order_by(PriceDaily.trade_date)
        .all()
    )

    return [
        PricePoint(
            trade_date=p.trade_date,
            open=to_float(p.open),
            high=to_float(p.high),
            low=to_float(p.low),
            close=to_float(p.close),
            volume=to_int(p.volume),
        )
        for p in prices
    ]


@router.get("/{symbol}/indicators", response_model=list[IndicatorPoint])
def get_indicators(
    symbol: str,
    db: Session = Depends(get_db),
    period: Period = Query("3m", description="Period: 1m, 3m, 6m, 1y, 5y, all"),
):
    """Get technical indicators for a ticker."""
    ticker = _validate_ticker(symbol, db)
    cutoff = date.today() - timedelta(days=PERIOD_DAYS[period])

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
            sma_20=to_float(i.sma_20),
            sma_50=to_float(i.sma_50),
            sma_200=to_float(i.sma_200),
            ema_12=to_float(i.ema_12),
            ema_26=to_float(i.ema_26),
            rsi_14=to_float(i.rsi_14),
            macd=to_float(i.macd),
            macd_signal=to_float(i.macd_signal),
            macd_histogram=to_float(i.macd_histogram),
            bollinger_upper=to_float(i.bollinger_upper),
            bollinger_lower=to_float(i.bollinger_lower),
            atr_14=to_float(i.atr_14),
            volume_sma_20=to_float(i.volume_sma_20),
            relative_strength_spy=to_float(i.relative_strength_spy),
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
        market_cap=to_float(f.market_cap),
        pe_ratio=to_float(f.pe_ratio),
        forward_pe=to_float(f.forward_pe),
        ps_ratio=to_float(f.ps_ratio),
        pb_ratio=to_float(f.pb_ratio),
        ev_ebitda=to_float(f.ev_ebitda),
        profit_margin=to_float(f.profit_margin),
        operating_margin=to_float(f.operating_margin),
        return_on_equity=to_float(f.return_on_equity),
        revenue_growth_yoy=to_float(f.revenue_growth_yoy),
        eps_ttm=to_float(f.eps_ttm),
        debt_to_equity=to_float(f.debt_to_equity),
        dividend_yield=to_float(f.dividend_yield),
        beta=to_float(f.beta),
    )


@router.get("/{symbol}/signals", response_model=TickerSignals)
def get_ticker_signals(
    symbol: str,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=3650),
    limit: int = Query(50, ge=1, le=500, description="Max items per category"),
):
    """Get all signal data for a specific ticker.

    Lists are capped at ``limit`` per category; ``counts`` holds the real
    (uncapped) number of events in the lookback window.
    """
    ticker = _validate_ticker(symbol, db)
    cutoff = date.today() - timedelta(days=days)

    ark_q = db.query(ARKDelta).filter(
        ARKDelta.ticker == ticker,
        ARKDelta.delta_date >= cutoff,
        ARKDelta.delta_type != "unchanged",
    )
    cluster_q = db.query(InsiderCluster).filter(
        InsiderCluster.ticker == ticker, InsiderCluster.cluster_end >= cutoff,
    )
    pol_q = db.query(PoliticianTrade).filter(
        PoliticianTrade.ticker == ticker,
        PoliticianTrade.disclosure_date >= cutoff,
    )
    rating_q = db.query(AnalystRating).filter(
        AnalystRating.ticker == ticker, AnalystRating.rating_date >= cutoff,
    )

    ark_deltas = ark_q.order_by(desc(ARKDelta.delta_date)).limit(limit).all()
    clusters = cluster_q.order_by(desc(InsiderCluster.cluster_score)).limit(limit).all()
    pol_trades = (
        pol_q.order_by(desc(PoliticianTrade.disclosure_date)).limit(limit).all()
    )
    ratings = rating_q.order_by(desc(AnalystRating.rating_date)).limit(limit).all()
    targets = consensus_targets(db, [ticker]) if ratings else {}

    return TickerSignals(
        ticker=ticker,
        days=days,
        ark_deltas=[ark_delta_item(d) for d in ark_deltas],
        insider_clusters=[insider_cluster_item(c) for c in clusters],
        politician_trades=[politician_trade_item(t) for t in pol_trades],
        analyst_ratings=[analyst_rating_item(r, targets) for r in ratings],
        counts=TickerSignalCounts(
            ark_deltas=ark_q.count(),
            insider_clusters=cluster_q.count(),
            politician_trades=pol_q.count(),
            analyst_ratings=rating_q.count(),
        ),
    )


def signal_update_dimension(
    scheduler_active: bool,
    last_status: str | None,
    next_run: datetime | None,
) -> DataQualityDimension:
    """Build the 'Signal-Updates' data quality dimension.

    ``last_status`` is the raw collection_log status of the latest price
    collector run; legacy values are normalized (e.g. ``error`` → failed).
    """
    status = job_status.normalize(last_status)
    next_str = next_run.strftime("%d.%m. %H:%M") if next_run else None

    if not scheduler_active:
        return DataQualityDimension(
            label="Signal-Updates", status="missing", summary="Scheduler nicht aktiv",
        )
    if status in (job_status.FAILED, job_status.PARTIAL):
        summary = (
            "Letzter Lauf fehlgeschlagen"
            if status == job_status.FAILED
            else "Letzter Lauf unvollständig"
        )
        if next_str:
            summary += f", nächster: {next_str}"
        return DataQualityDimension(
            label="Signal-Updates", status="partial", summary=summary,
        )
    summary = f"Nächster Lauf: {next_str}" if next_str else "Scheduler aktiv"
    return DataQualityDimension(
        label="Signal-Updates", status="complete", summary=summary,
    )


@router.get("/{symbol}/data-quality", response_model=TickerDataQuality)
def get_data_quality(
    symbol: str,
    db: Session = Depends(get_db),
    scheduler=Depends(get_scheduler),
):
    """Assess data quality/completeness for a ticker.

    Returns per-dimension status (prices, TA indicators, fundamentals,
    signal updates) with human-readable summaries.
    """
    ticker = _validate_ticker(symbol, db)
    today = date.today()
    dimensions: list[DataQualityDimension] = []

    # ── 1. Preise ────────────────────────────────────────────────────
    price_stats = (
        db.query(
            func.count(PriceDaily.trade_date),
            func.max(PriceDaily.trade_date),
        )
        .filter(PriceDaily.ticker == ticker)
        .first()
    )
    price_count = price_stats[0] if price_stats else 0
    price_latest = price_stats[1] if price_stats else None

    if price_count == 0:
        p_status, p_summary = "missing", "Keine Preisdaten vorhanden"
    else:
        days_ago = (today - price_latest).days if price_latest else 999
        formatted_date = price_latest.strftime("%d.%m.%Y") if price_latest else "—"
        p_summary = (
            f"{price_count:,} Tage verfügbar, "
            f"letztes Update {formatted_date}"
        ).replace(",", ".")
        if price_count >= 200 and days_ago <= 3:
            p_status = "complete"
        elif price_count > 0 and days_ago <= 7:
            p_status = "partial"
        else:
            p_status = "partial"

    dimensions.append(
        DataQualityDimension(label="Preise", status=p_status, summary=p_summary)
    )

    # ── 2. Technische Indikatoren ────────────────────────────────────
    ta_latest = (
        db.query(func.max(TechnicalIndicator.trade_date))
        .filter(TechnicalIndicator.ticker == ticker)
        .scalar()
    )

    if ta_latest is None:
        t_status, t_summary = "missing", "Keine Indikatordaten vorhanden"
    else:
        ta_days_ago = (today - ta_latest).days
        formatted_ta = ta_latest.strftime("%d.%m.%Y")
        t_summary = f"Berechnet bis {formatted_ta}"
        t_status = "complete" if ta_days_ago <= 3 else "partial"

    dimensions.append(
        DataQualityDimension(
            label="TA-Indikatoren", status=t_status, summary=t_summary
        )
    )

    # ── 3. Fundamentals ──────────────────────────────────────────────
    fund_latest = (
        db.query(func.max(FundamentalsSnapshot.snapshot_date))
        .filter(FundamentalsSnapshot.ticker == ticker)
        .scalar()
    )

    if fund_latest is None:
        f_status, f_summary = "missing", "Noch nicht erfasst"
    else:
        fund_days = (today - fund_latest).days
        formatted_fund = fund_latest.strftime("%d.%m.%Y")
        f_summary = f"Letzter Snapshot {formatted_fund}"
        f_status = "complete" if fund_days <= 14 else "partial"

    dimensions.append(
        DataQualityDimension(
            label="Fundamentals", status=f_status, summary=f_summary
        )
    )

    # ── 4. Signal-Updates (Scheduler-Status) ─────────────────────────
    next_price_run = None
    scheduler_active = False
    if scheduler and scheduler.running:
        scheduler_active = True
        for job in scheduler.get_jobs():
            if job.id == PRICE_JOB_ID:
                next_price_run = job.next_run_time
                break

    # Check last collection log of the price collector
    last_log = (
        db.query(CollectionLog)
        .filter(CollectionLog.collector_name == PRICE_COLLECTOR_NAME)
        .order_by(desc(CollectionLog.started_at))
        .first()
    )

    dimensions.append(
        signal_update_dimension(
            scheduler_active,
            last_log.status if last_log else None,
            next_price_run,
        )
    )

    # ── Overall completeness ─────────────────────────────────────────
    complete_count = sum(1 for d in dimensions if d.status == "complete")
    overall = complete_count / len(dimensions) if dimensions else 0.0

    return TickerDataQuality(
        ticker=ticker,
        dimensions=dimensions,
        overall_completeness=round(overall, 2),
    )
