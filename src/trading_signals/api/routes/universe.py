"""Universe API routes.

Provides ticker listing with filtering, pagination, and detail views.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, or_
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

    # Default: show only active tickers
    if active is None:
        query = query.filter(Universe.is_active.is_(True))
    else:
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
                price_change_pct=price_change_pct(
                    price_info.get("close"), price_info.get("prev_close")
                ),
            )
        )

    return UniverseResponse(
        tickers=tickers,
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/sectors", response_model=list[str])
def list_sectors(db: Session = Depends(get_db)):
    """Get all distinct sectors in the universe."""
    sectors = (
        db.query(Universe.sector)
        .filter(Universe.is_active.is_(True))
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
        raise HTTPException(
            status_code=404, detail=f"Ticker {ticker.upper()} not found"
        )

    price_info = _get_latest_prices(db, [t.ticker]).get(t.ticker, {})

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
        price_change_pct=price_change_pct(
            price_info.get("close"), price_info.get("prev_close")
        ),
    )


def price_change_pct(close: float | None, prev_close: float | None) -> float | None:
    """Daily change in percent; ``None`` if either price is missing/invalid."""
    if close is None or prev_close is None or prev_close <= 0:
        return None
    return round((close - prev_close) / prev_close * 100, 2)


def build_price_map(rows) -> dict[str, dict]:
    """Fold ``(ticker, close, trade_date, rn)`` rows into a price map.

    ``rn`` = 1 is the latest trading day, ``rn`` = 2 the previous one.
    """
    result: dict[str, dict] = {}
    for row in rows:
        entry = result.setdefault(
            row.ticker, {"close": None, "trade_date": None, "prev_close": None}
        )
        close = float(row.close) if row.close is not None else None
        if row.rn == 1:
            entry["close"] = close
            entry["trade_date"] = row.trade_date
        elif row.rn == 2:
            entry["prev_close"] = close
    return result


def _get_latest_prices(
    db: Session, tickers: list[str]
) -> dict[str, dict]:
    """Get the latest price for a list of tickers efficiently.

    Returns a dict: {ticker: {close, trade_date, prev_close}}
    Uses a single ROW_NUMBER() window query to get the two most recent
    prices per ticker (no per-ticker follow-up queries).
    """
    if not tickers:
        return {}

    rn = (
        func.row_number()
        .over(partition_by=PriceDaily.ticker, order_by=desc(PriceDaily.trade_date))
        .label("rn")
    )
    ranked = (
        db.query(
            PriceDaily.ticker.label("ticker"),
            PriceDaily.close.label("close"),
            PriceDaily.trade_date.label("trade_date"),
            rn,
        )
        .filter(PriceDaily.ticker.in_(tickers))
        .subquery()
    )
    rows = (
        db.query(ranked.c.ticker, ranked.c.close, ranked.c.trade_date, ranked.c.rn)
        .filter(ranked.c.rn <= 2)
        .all()
    )
    return build_price_map(rows)
