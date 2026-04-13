"""Universe API routes.

Provides ticker listing with filtering, pagination, and detail views.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db
from trading_signals.api.schemas import TickerDetail, TickerSummary, UniverseResponse
from trading_signals.db.models import PriceDaily, Universe

router = APIRouter(prefix="/universe")


@router.get("", response_model=UniverseResponse)
def list_tickers(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=500, description="Items per page"),
    index: str | None = Query(None, description="Filter by index (sp500, nasdaq100)"),
    sector: str | None = Query(None, description="Filter by sector"),
    active: bool | None = Query(None, description="Filter by active status"),
    search: str | None = Query(None, description="Search by ticker or name"),
):
    """List all tickers in the universe with optional filters."""
    query = db.query(Universe)

    # Apply filters
    if active is not None:
        query = query.filter(Universe.is_active == active)
    if sector:
        query = query.filter(Universe.sector == sector)
    if index:
        query = query.filter(Universe.index_membership.contains([index]))
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Universe.ticker.ilike(search_term),
                Universe.company_name.ilike(search_term),
            )
        )

    # Count total before pagination
    total = query.count()

    # Sort and paginate
    tickers_orm = (
        query.order_by(Universe.ticker)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    # Get latest prices for these tickers in one query
    ticker_symbols = [t.ticker for t in tickers_orm]
    latest_prices = _get_latest_prices(db, ticker_symbols)

    tickers = []
    for t in tickers_orm:
        price_info = latest_prices.get(t.ticker, {})
        tickers.append(
            TickerSummary(
                ticker=t.ticker,
                company_name=t.company_name,
                exchange=t.exchange,
                sector=t.sector,
                industry=t.industry,
                is_active=t.is_active,
                added_date=t.added_date,
                added_by=t.added_by,
                index_membership=t.index_membership or [],
                last_price=price_info.get("close"),
                last_price_date=price_info.get("trade_date"),
            )
        )

    return UniverseResponse(
        tickers=tickers,
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/sectors")
def list_sectors(db: Session = Depends(get_db)):
    """Get all distinct sectors in the universe."""
    sectors = (
        db.query(Universe.sector)
        .filter(Universe.sector.isnot(None))
        .distinct()
        .order_by(Universe.sector)
        .all()
    )
    return [s[0] for s in sectors]


@router.get("/{ticker}", response_model=TickerDetail)
def get_ticker(ticker: str, db: Session = Depends(get_db)):
    """Get detailed information for a single ticker."""
    t = db.query(Universe).filter(Universe.ticker == ticker.upper()).first()
    if not t:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")

    price_info = _get_latest_prices(db, [t.ticker]).get(t.ticker, {})

    # Calculate daily price change
    price_change_pct = None
    if price_info.get("close") and price_info.get("prev_close"):
        prev = price_info["prev_close"]
        if prev and prev > 0:
            price_change_pct = round(
                (price_info["close"] - prev) / prev * 100, 2
            )

    return TickerDetail(
        ticker=t.ticker,
        company_name=t.company_name,
        exchange=t.exchange,
        sector=t.sector,
        industry=t.industry,
        is_active=t.is_active,
        added_date=t.added_date,
        added_by=t.added_by,
        index_membership=t.index_membership or [],
        last_price=price_info.get("close"),
        last_price_date=price_info.get("trade_date"),
        price_change_pct=price_change_pct,
    )


def _get_latest_prices(
    db: Session, tickers: list[str]
) -> dict[str, dict]:
    """Get the latest price for a list of tickers efficiently.

    Returns a dict: {ticker: {close, trade_date, prev_close}}
    Uses a window function to get the two most recent prices.
    """
    if not tickers:
        return {}

    from sqlalchemy import desc

    # Get the latest 2 prices per ticker using a subquery
    result = {}
    # Batch query: latest price per ticker

    latest_subq = (
        db.query(
            PriceDaily.ticker,
            func.max(PriceDaily.trade_date).label("max_date"),
        )
        .filter(PriceDaily.ticker.in_(tickers))
        .group_by(PriceDaily.ticker)
        .subquery()
    )

    latest_prices = (
        db.query(PriceDaily)
        .join(
            latest_subq,
            (PriceDaily.ticker == latest_subq.c.ticker)
            & (PriceDaily.trade_date == latest_subq.c.max_date),
        )
        .all()
    )

    for p in latest_prices:
        # Get previous day's close for change calculation
        prev = (
            db.query(PriceDaily.close)
            .filter(
                PriceDaily.ticker == p.ticker,
                PriceDaily.trade_date < p.trade_date,
            )
            .order_by(desc(PriceDaily.trade_date))
            .first()
        )

        result[p.ticker] = {
            "close": float(p.close) if p.close else None,
            "trade_date": p.trade_date,
            "prev_close": float(prev[0]) if prev and prev[0] else None,
        }

    return result
