"""Signals API routes.

Provides recent signal data: ARK deltas, insider clusters,
politician trades, analyst ratings, and news sentiment.

List endpoints that apply a ``limit`` report the uncapped number of
matching rows in the ``X-Total-Count`` response header so clients can
show "N von M" instead of silently truncating.
"""

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import case, desc, func
from sqlalchemy.orm import Query as SAQuery
from sqlalchemy.orm import Session

from trading_signals.api.deps import get_db
from trading_signals.api.schemas import (
    AnalystRatingItem,
    ARKDeltaItem,
    ARKSummaryItem,
    InsiderClusterItem,
    PoliticianTradeItem,
    SentimentArticleItem,
    SentimentSummaryItem,
)
from trading_signals.api.serializers import (
    analyst_rating_item,
    ark_delta_item,
    consensus_targets,
    insider_cluster_item,
    politician_trade_item,
    to_float,
)
from trading_signals.db.models import (
    AnalystRating,
    ARKDelta,
    InsiderCluster,
    PoliticianTrade,
)
from trading_signals.db.models.news import NewsArticle, NewsSentiment

router = APIRouter(prefix="/signals")

TOTAL_COUNT_HEADER = "X-Total-Count"

SentimentSort = Literal["most_negative", "most_positive", "most_articles"]


def _set_total(response: Response, query: SAQuery) -> None:
    """Set the uncapped row count of ``query`` as response header."""
    response.headers[TOTAL_COUNT_HEADER] = str(query.order_by(None).count())


def _norm_ticker(ticker: str | None) -> str | None:
    ticker = (ticker or "").strip().upper()
    return ticker or None


@router.get("/ark", response_model=list[ARKDeltaItem])
def get_ark_deltas(
    response: Response,
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="Lookback days"),
    ticker: str | None = Query(None, description="Exact ticker filter"),
    limit: int = Query(100, ge=1, le=500),
):
    """Get recent ARK ETF delta movements.

    Shows new positions, closed positions, and significant weight changes.
    """
    cutoff = date.today() - timedelta(days=days)
    query = db.query(ARKDelta).filter(
        ARKDelta.delta_date >= cutoff,
        ARKDelta.delta_type != "unchanged",
    )
    if symbol := _norm_ticker(ticker):
        query = query.filter(ARKDelta.ticker == symbol)
    _set_total(response, query)
    deltas = (
        query.order_by(desc(ARKDelta.delta_date), ARKDelta.ticker).limit(limit).all()
    )
    return [ark_delta_item(d) for d in deltas]


def summarize_ark_deltas(deltas: list[ARKDelta]) -> list[ARKSummaryItem]:
    """Aggregate ARK deltas per ticker, strongest absolute weight move first."""
    ticker_data: dict[str, list[ARKDelta]] = {}
    for d in deltas:
        ticker_data.setdefault(d.ticker, []).append(d)

    results = []
    for ticker, entries in ticker_data.items():
        total_shares = sum(float(e.shares_delta or 0) for e in entries)
        total_weight = sum(float(e.weight_delta or 0) for e in entries)
        etfs = sorted({e.etf_ticker for e in entries})
        dates = sorted({e.delta_date for e in entries})

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


@router.get("/ark/summary", response_model=list[ARKSummaryItem])
def get_ark_summary(
    db: Session = Depends(get_db),
    days: int = Query(5, ge=1, le=90, description="Lookback window in days"),
    ticker: str | None = Query(None, description="Exact ticker filter"),
):
    """Get aggregated ARK moves per ticker across all ETFs.

    Groups ARK delta entries by ticker over the given time window,
    summing shares and weight changes across all ETFs and days.
    Sorted by absolute weight impact (strongest moves first). Not limited.
    """
    cutoff = date.today() - timedelta(days=days)
    query = db.query(ARKDelta).filter(
        ARKDelta.delta_date >= cutoff,
        ARKDelta.delta_type != "unchanged",
    )
    if symbol := _norm_ticker(ticker):
        query = query.filter(ARKDelta.ticker == symbol)
    return summarize_ark_deltas(query.all())


@router.get("/insider", response_model=list[InsiderClusterItem])
def get_insider_clusters(
    response: Response,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365, description="Lookback days"),
    min_score: float = Query(0.0, ge=0.0, description="Minimum cluster score"),
    ticker: str | None = Query(None, description="Exact ticker filter"),
    limit: int = Query(50, ge=1, le=500),
):
    """Get active insider trading clusters.

    Returns clusters where multiple insiders traded the same stock
    within a short time window.
    """
    cutoff = date.today() - timedelta(days=days)
    query = (
        db.query(InsiderCluster)
        .filter(InsiderCluster.cluster_end >= cutoff)
        .filter(InsiderCluster.cluster_score >= min_score)
    )
    if symbol := _norm_ticker(ticker):
        query = query.filter(InsiderCluster.ticker == symbol)
    _set_total(response, query)
    clusters = query.order_by(desc(InsiderCluster.cluster_score)).limit(limit).all()
    return [insider_cluster_item(c) for c in clusters]


@router.get("/politicians", response_model=list[PoliticianTradeItem])
def get_politician_trades(
    response: Response,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365, description="Lookback days"),
    ticker: str | None = Query(None, description="Exact ticker filter"),
    limit: int = Query(100, ge=1, le=500),
):
    """Get recent politician trades from Senate financial disclosures."""
    cutoff = date.today() - timedelta(days=days)
    query = db.query(PoliticianTrade).filter(PoliticianTrade.disclosure_date >= cutoff)
    if symbol := _norm_ticker(ticker):
        query = query.filter(PoliticianTrade.ticker == symbol)
    _set_total(response, query)
    trades = query.order_by(desc(PoliticianTrade.disclosure_date)).limit(limit).all()
    return [politician_trade_item(t) for t in trades]


@router.get("/ratings", response_model=list[AnalystRatingItem])
def get_analyst_ratings(
    response: Response,
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=365, description="Lookback days"),
    ticker: str | None = Query(None, description="Exact ticker filter"),
    limit: int = Query(100, ge=1, le=500),
):
    """Get recent analyst rating changes (upgrades/downgrades).

    ``firm_target_new/old`` are the firm's own price targets;
    ``consensus_target`` is the consensus median from the latest
    fundamentals snapshot of that ticker (context only).
    """
    cutoff = date.today() - timedelta(days=days)
    query = db.query(AnalystRating).filter(AnalystRating.rating_date >= cutoff)
    if symbol := _norm_ticker(ticker):
        query = query.filter(AnalystRating.ticker == symbol)
    _set_total(response, query)
    ratings = query.order_by(desc(AnalystRating.rating_date)).limit(limit).all()

    targets = consensus_targets(db, (r.ticker for r in ratings))
    return [analyst_rating_item(r, targets) for r in ratings]


# ── Sentiment Signal Endpoints ───────────────────────────────────────


def _sentiment_order(sort: SentimentSort, avg_col, cnt_col) -> tuple:
    if sort == "most_positive":
        return (desc(avg_col), NewsSentiment.ticker)
    if sort == "most_articles":
        return (desc(cnt_col), NewsSentiment.ticker)
    return (avg_col, NewsSentiment.ticker)


@router.get("/sentiment/summary", response_model=list[SentimentSummaryItem])
def get_sentiment_summary(
    response: Response,
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="Lookback days"),
    ticker: str | None = Query(None, description="Exact ticker filter"),
    sort: SentimentSort = Query("most_negative", description="Sort order"),
    limit: int = Query(100, ge=1, le=1000),
):
    """Aggregated sentiment per ticker over a time window.

    For each ticker with scored articles, returns: average sentiment,
    article count, positive/negative/neutral breakdown, and the most
    recent headline.
    """
    cutoff = date.today() - timedelta(days=days)
    symbol = _norm_ticker(ticker)

    base_filter = [
        NewsArticle.published_at >= cutoff,
        NewsSentiment.ticker.isnot(None),
    ]
    if symbol:
        base_filter.append(NewsSentiment.ticker == symbol)

    avg_col = func.avg(NewsSentiment.sentiment_score)
    cnt_col = func.count(NewsSentiment.id)

    # Total number of tickers (before limit)
    total = (
        db.query(func.count(func.distinct(NewsSentiment.ticker)))
        .join(NewsArticle, NewsSentiment.article_id == NewsArticle.id)
        .filter(*base_filter)
        .scalar()
    ) or 0
    response.headers[TOTAL_COUNT_HEADER] = str(total)

    # Join articles + sentiment, grouped by ticker
    rows = (
        db.query(
            NewsSentiment.ticker,
            avg_col.label("avg_score"),
            cnt_col.label("cnt"),
            func.sum(case(
                (NewsSentiment.sentiment_label == "negative", 1), else_=0
            )).label("neg"),
            func.sum(case(
                (NewsSentiment.sentiment_label == "positive", 1), else_=0
            )).label("pos"),
            func.sum(case(
                (NewsSentiment.sentiment_label == "neutral", 1), else_=0
            )).label("neu"),
        )
        .join(NewsArticle, NewsSentiment.article_id == NewsArticle.id)
        .filter(*base_filter)
        .group_by(NewsSentiment.ticker)
        .order_by(*_sentiment_order(sort, avg_col, cnt_col))
        .limit(limit)
        .all()
    )

    # Latest headline per ticker – single DISTINCT ON query (no N+1)
    tickers = [r.ticker for r in rows]
    latest_headlines: dict[str, tuple] = {}
    if tickers:
        latest_rows = (
            db.query(
                NewsSentiment.ticker,
                NewsArticle.headline,
                NewsSentiment.sentiment_label,
                NewsArticle.published_at,
            )
            .join(NewsArticle, NewsSentiment.article_id == NewsArticle.id)
            .filter(
                NewsSentiment.ticker.in_(tickers),
                NewsArticle.published_at >= cutoff,
            )
            .distinct(NewsSentiment.ticker)
            .order_by(NewsSentiment.ticker, desc(NewsArticle.published_at))
            .all()
        )
        for latest in latest_rows:
            latest_headlines[latest.ticker] = (
                latest.headline,
                latest.sentiment_label,
                latest.published_at.date() if latest.published_at else None,
            )

    items = []
    for r in rows:
        cnt = int(r.cnt or 0)
        neg = int(r.neg or 0)
        pos = int(r.pos or 0)
        neu = int(r.neu or 0)
        headline_info = latest_headlines.get(r.ticker)

        items.append(SentimentSummaryItem(
            ticker=r.ticker,
            avg_sentiment=(
                round(float(r.avg_score), 4) if r.avg_score is not None else None
            ),
            article_count=cnt,
            negative_count=neg,
            positive_count=pos,
            neutral_count=neu,
            neg_pct=round(neg / cnt * 100, 1) if cnt > 0 else 0.0,
            latest_headline=headline_info[0] if headline_info else None,
            latest_sentiment_label=headline_info[1] if headline_info else None,
            latest_date=headline_info[2] if headline_info else None,
        ))

    return items


@router.get("/sentiment/articles", response_model=list[SentimentArticleItem])
def get_sentiment_articles(
    response: Response,
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="Lookback days"),
    ticker: str | None = Query(None, description="Filter by ticker"),
    limit: int = Query(100, ge=1, le=500),
):
    """Individual news articles with their sentiment scores.

    Optionally filtered by ticker. Sorted by publication date (newest first).
    """
    cutoff = date.today() - timedelta(days=days)

    query = (
        db.query(
            NewsArticle.article_id,
            NewsArticle.headline,
            NewsArticle.source,
            NewsArticle.published_at,
            NewsArticle.article_url,
            NewsSentiment.ticker,
            NewsSentiment.sentiment_score,
            NewsSentiment.sentiment_label,
        )
        .join(NewsSentiment, NewsSentiment.article_id == NewsArticle.id)
        .filter(NewsArticle.published_at >= cutoff)
    )

    if symbol := _norm_ticker(ticker):
        query = query.filter(NewsSentiment.ticker == symbol)

    _set_total(response, query)
    articles = (
        query
        .order_by(desc(NewsArticle.published_at))
        .limit(limit)
        .all()
    )

    return [
        SentimentArticleItem(
            article_id=str(a.article_id),
            headline=a.headline,
            source=a.source,
            published_at=a.published_at,
            ticker=a.ticker,
            sentiment_score=to_float(a.sentiment_score),
            sentiment_label=a.sentiment_label,
            url=a.article_url,
        )
        for a in articles
    ]
