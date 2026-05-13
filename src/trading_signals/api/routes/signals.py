"""Signals API routes.

Provides recent signal data: ARK deltas, insider clusters,
politician trades, analyst ratings, and news sentiment.
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
    SentimentSummaryItem,
    SentimentArticleItem,
)
from trading_signals.db.models import (
    AnalystRating,
    ARKDelta,
    InsiderCluster,
    PoliticianTrade,
)
from trading_signals.db.models.fundamentals import FundamentalsSnapshot
from trading_signals.db.models.news import NewsArticle, NewsSentiment

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


# ── Sentiment Signal Endpoints ───────────────────────────────────────


@router.get("/sentiment/summary", response_model=list[SentimentSummaryItem])
def get_sentiment_summary(
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="Lookback days"),
    limit: int = Query(50, ge=1, le=200),
):
    """Aggregated sentiment per ticker over a time window.

    For each ticker with scored articles, returns: average sentiment,
    article count, positive/negative/neutral breakdown, and the most
    recent headline.
    """
    cutoff = date.today() - timedelta(days=days)

    # Join articles + sentiment, grouped by ticker
    rows = (
        db.query(
            NewsSentiment.ticker,
            func.avg(NewsSentiment.sentiment_score).label("avg_score"),
            func.count(NewsSentiment.id).label("cnt"),
            func.sum(
                func.cast(
                    NewsSentiment.sentiment_label == "negative", BigInteger
                )
            ).label("neg"),
            func.sum(
                func.cast(
                    NewsSentiment.sentiment_label == "positive", BigInteger
                )
            ).label("pos"),
            func.sum(
                func.cast(
                    NewsSentiment.sentiment_label == "neutral", BigInteger
                )
            ).label("neu"),
        )
        .join(NewsArticle, NewsSentiment.article_id == NewsArticle.id)
        .filter(
            NewsArticle.published_at >= cutoff,
            NewsSentiment.ticker.isnot(None),
        )
        .group_by(NewsSentiment.ticker)
        .order_by(func.avg(NewsSentiment.sentiment_score))
        .limit(limit)
        .all()
    )

    # Collect tickers for latest headline lookup
    tickers = [r.ticker for r in rows]

    # Subquery: latest article per ticker
    latest_headlines: dict[str, tuple] = {}
    if tickers:
        for ticker in tickers:
            latest = (
                db.query(
                    NewsArticle.headline,
                    NewsSentiment.sentiment_label,
                    NewsArticle.published_at,
                )
                .join(NewsSentiment, NewsSentiment.article_id == NewsArticle.id)
                .filter(
                    NewsSentiment.ticker == ticker,
                    NewsArticle.published_at >= cutoff,
                )
                .order_by(desc(NewsArticle.published_at))
                .first()
            )
            if latest:
                latest_headlines[ticker] = (
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
            avg_sentiment=round(float(r.avg_score), 4) if r.avg_score else None,
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

    if ticker:
        query = query.filter(NewsSentiment.ticker == ticker.upper())

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
            sentiment_score=float(a.sentiment_score) if a.sentiment_score is not None else None,
            sentiment_label=a.sentiment_label,
            url=a.article_url,
        )
        for a in articles
    ]
