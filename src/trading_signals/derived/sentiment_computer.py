"""Sentiment Computer – scores unscored news articles.

Orchestrates the sentiment scoring pipeline:
  1. Find news articles not yet scored by the current model
  2. Score headlines in batches via SentimentScorer
  3. Store results in news_sentiment table (one row per ticker per article)

For multi-ticker articles (e.g. "AAPL and MSFT announce partnership"),
one sentiment row is created per mentioned ticker. Global articles
(no symbols) get a row with ticker=NULL.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.db.models.news import NewsArticle, NewsSentiment
from trading_signals.derived.sentiment_scorer import SentimentResult, SentimentScorer
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# Process articles in batches for efficient scoring
SCORING_BATCH_SIZE = 100


class SentimentComputer:
    """Score unscored news articles and store sentiment results."""

    def __init__(self, session: Session, scorer: SentimentScorer) -> None:
        self.session = session
        self.scorer = scorer

    def compute(self) -> int:
        """Score all articles not yet processed by the current model.

        Returns:
            Number of sentiment score rows written.
        """
        model_version = self.scorer.model_version

        # Find articles not yet scored by this model version
        unscored = self._get_unscored_articles(model_version)
        if not unscored:
            logger.info(
                f"[sentiment_computer] No unscored articles for "
                f"model={model_version}"
            )
            return 0

        logger.info(
            f"[sentiment_computer] Scoring {len(unscored)} articles "
            f"with model={model_version}"
        )

        total_written = 0

        # Process in batches
        for i in range(0, len(unscored), SCORING_BATCH_SIZE):
            batch = unscored[i : i + SCORING_BATCH_SIZE]
            batch_num = (i // SCORING_BATCH_SIZE) + 1
            total_batches = (
                len(unscored) + SCORING_BATCH_SIZE - 1
            ) // SCORING_BATCH_SIZE

            # Score headlines
            headlines = [a.headline for a in batch]
            try:
                results = self.scorer.score_batch(headlines)
            except Exception as e:
                logger.error(
                    f"[sentiment_computer] Batch {batch_num}/{total_batches} "
                    f"scoring failed: {e}"
                )
                continue

            # Store results
            written = self._store_results(batch, results, model_version)
            total_written += written

            if batch_num % 5 == 0 or batch_num == total_batches:
                logger.info(
                    f"[sentiment_computer] Batch {batch_num}/{total_batches}: "
                    f"{written} scores written"
                )

        self.session.flush()
        logger.info(
            f"[sentiment_computer] Completed: {total_written} sentiment scores "
            f"written for {len(unscored)} articles"
        )
        return total_written

    def _get_unscored_articles(
        self, model_version: str
    ) -> list[NewsArticle]:
        """Find articles that haven't been scored by this model yet.

        Uses a LEFT JOIN / IS NULL pattern to find articles without
        a corresponding entry in news_sentiment for this model.
        """
        # Subquery: article_ids already scored by this model
        scored_ids = (
            select(NewsSentiment.article_id)
            .where(NewsSentiment.model_version == model_version)
            .distinct()
            .subquery()
        )

        stmt = (
            select(NewsArticle)
            .outerjoin(scored_ids, NewsArticle.id == scored_ids.c.article_id)
            .where(scored_ids.c.article_id.is_(None))
            .order_by(NewsArticle.published_at.desc())
        )

        return list(self.session.execute(stmt).scalars().all())

    def _store_results(
        self,
        articles: list[NewsArticle],
        results: list[SentimentResult],
        model_version: str,
    ) -> int:
        """Store sentiment results for a batch of articles.

        For each article:
        - If symbols are present: one row per ticker
        - If no symbols (global news): one row with ticker=NULL

        Returns:
            Number of rows written.
        """
        written = 0

        for article, result in zip(articles, results):
            tickers = article.symbols or []

            if not tickers:
                # Global news: single row with ticker=NULL
                written += self._upsert_sentiment(
                    article_id=article.id,
                    ticker=None,
                    result=result,
                    model_version=model_version,
                )
            else:
                # Ticker-specific: one row per mentioned ticker
                for ticker in tickers:
                    written += self._upsert_sentiment(
                        article_id=article.id,
                        ticker=ticker,
                        result=result,
                        model_version=model_version,
                    )

        return written

    def _upsert_sentiment(
        self,
        article_id: int,
        ticker: str | None,
        result: SentimentResult,
        model_version: str,
    ) -> int:
        """Insert or update a single sentiment row.

        Uses ON CONFLICT on (article_id, ticker, model_version) to
        handle re-runs gracefully.

        Returns:
            1 if a row was written/updated, 0 otherwise.
        """
        stmt = (
            pg_insert(NewsSentiment)
            .values(
                article_id=article_id,
                ticker=ticker,
                sentiment_label=result.label,
                sentiment_score=result.score,
                confidence=result.confidence,
                model_version=model_version,
            )
            .on_conflict_do_update(
                constraint="uq_news_sentiment_article_ticker_model",
                set_={
                    "sentiment_label": result.label,
                    "sentiment_score": result.score,
                    "confidence": result.confidence,
                },
            )
        )
        self.session.execute(stmt)
        return 1
