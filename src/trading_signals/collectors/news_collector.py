"""News Collector using Alpaca News API.

Fetches financial news articles for all active tickers in the universe
plus global market news (articles without specific ticker association).

Data source:
  - Alpaca News API v1beta1 (Benzinga partnership)
  - Endpoint: GET https://data.alpaca.markets/v1beta1/news
  - Auth: Existing Alpaca API keys (APCA-API-KEY-ID + APCA-API-SECRET-KEY)
  - Historical coverage: back to 2015

Features:
  - Batch fetching: universe tickers in groups of 50 symbols
  - Global market news: separate fetch without symbol filter
  - Deduplication via article_id (ON CONFLICT DO NOTHING)
  - Configurable lookback window (default: 24h for daily runs)
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.config import get_settings
from trading_signals.db.models.news import NewsArticle
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

# Alpaca News API base URL
NEWS_BASE_URL = "https://data.alpaca.markets/v1beta1/news"

# Number of symbols per Alpaca news request
SYMBOL_BATCH_SIZE = 50

# Max articles per page (Alpaca limit)
PAGE_LIMIT = 50

# Default lookback: 1 day for daily collection
DEFAULT_LOOKBACK_HOURS = 36  # 36h buffer for timezone edge cases


class NewsCollectorAlpaca(BaseCollector):
    """Collect financial news from Alpaca News API."""

    name = "news_alpaca"

    def __init__(self, lookback_hours: int = DEFAULT_LOOKBACK_HOURS) -> None:
        """Initialize the Alpaca news collector.

        Args:
            lookback_hours: Hours to look back for news articles.
                            Default 36 provides buffer for overnight runs.
        """
        self.lookback_hours = lookback_hours
        settings = get_settings()
        self._headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }

    def fetch(self, session: Session) -> list[dict]:
        """Fetch news articles for all universe tickers + global news.

        Returns:
            List of article dicts, deduplicated by article_id.
        """
        # Get all active tickers
        tickers = [
            r[0]
            for r in session.execute(
                Universe.__table__.select()
                .with_only_columns(Universe.ticker)
                .where(Universe.is_active.is_(True))
                .order_by(Universe.ticker)
            ).all()
        ]
        logger.info(
            f"[{self.name}] Fetching news for {len(tickers)} tickers "
            f"(lookback={self.lookback_hours}h)"
        )

        start_time = datetime.now(timezone.utc) - timedelta(
            hours=self.lookback_hours
        )
        seen_ids: set[str] = set()
        all_articles: list[dict] = []

        # 1. Fetch ticker-specific news in batches
        for i in range(0, len(tickers), SYMBOL_BATCH_SIZE):
            batch = tickers[i : i + SYMBOL_BATCH_SIZE]
            batch_num = (i // SYMBOL_BATCH_SIZE) + 1
            total_batches = (
                len(tickers) + SYMBOL_BATCH_SIZE - 1
            ) // SYMBOL_BATCH_SIZE

            logger.info(
                f"[{self.name}] Batch {batch_num}/{total_batches}: "
                f"{len(batch)} symbols"
            )

            try:
                articles = _fetch_news_page(
                    symbols=batch,
                    start=start_time,
                    headers=self._headers,
                )
                for article in articles:
                    aid = str(article.get("id", ""))
                    if aid and aid not in seen_ids:
                        seen_ids.add(aid)
                        all_articles.append(article)
            except Exception as e:
                logger.error(
                    f"[{self.name}] Batch {batch_num} failed: {e}"
                )

        # 2. Fetch global news (no symbol filter)
        logger.info(f"[{self.name}] Fetching global market news...")
        try:
            global_articles = _fetch_news_page(
                symbols=None,
                start=start_time,
                headers=self._headers,
            )
            for article in global_articles:
                aid = str(article.get("id", ""))
                if aid and aid not in seen_ids:
                    seen_ids.add(aid)
                    all_articles.append(article)
        except Exception as e:
            logger.error(f"[{self.name}] Global news fetch failed: {e}")

        logger.info(
            f"[{self.name}] Fetched {len(all_articles)} unique articles "
            f"(from {len(seen_ids)} total)"
        )
        return all_articles

    def store(
        self, session: Session, data: list[dict]
    ) -> tuple[int, int]:
        """Store fetched news articles in the database.

        Uses ON CONFLICT DO NOTHING on article_id for idempotent inserts.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        records_written = 0

        for article in data:
            article_id = str(article.get("id", ""))
            headline = article.get("headline", "")
            if not article_id or not headline:
                continue

            symbols = article.get("symbols", []) or []
            published_str = article.get("created_at", "")
            published_at = _parse_timestamp(published_str)
            if published_at is None:
                continue

            stmt = (
                pg_insert(NewsArticle)
                .values(
                    article_id=article_id,
                    headline=headline,
                    summary=article.get("summary"),
                    source=article.get("source"),
                    author=article.get("author"),
                    published_at=published_at,
                    article_url=article.get("url"),
                    symbols=symbols if symbols else None,
                    is_global=len(symbols) == 0,
                )
                .on_conflict_do_nothing(
                    constraint="uq_news_articles_article_id"
                )
            )
            result = session.execute(stmt)
            if result.rowcount > 0:
                records_written += 1

        session.flush()
        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} articles "
            f"({records_fetched - records_written} already existed)"
        )
        return records_fetched, records_written


@retry(max_attempts=3, base_delay=2.0)
def _fetch_news_page(
    symbols: list[str] | None,
    start: datetime,
    headers: dict[str, str],
    max_pages: int = 10,
) -> list[dict]:
    """Fetch news articles from Alpaca, handling pagination.

    Args:
        symbols: List of ticker symbols (max 50), or None for global news.
        start: Start time for news lookback.
        headers: Alpaca auth headers.
        max_pages: Maximum number of pages to fetch (safety limit).

    Returns:
        List of article dicts from the API.
    """
    all_articles: list[dict] = []
    next_page_token = None

    for _page in range(max_pages):
        params: dict[str, Any] = {
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": PAGE_LIMIT,
            "sort": "desc",
        }
        if symbols:
            params["symbols"] = ",".join(symbols)
        if next_page_token:
            params["page_token"] = next_page_token

        response = requests.get(
            NEWS_BASE_URL,
            headers=headers,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()

        articles = payload.get("news", [])
        all_articles.extend(articles)

        next_page_token = payload.get("next_page_token")
        if not next_page_token:
            break

    return all_articles


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse Alpaca timestamp (ISO 8601) to datetime."""
    if not ts:
        return None
    try:
        # Alpaca returns: "2026-05-12T14:30:00Z"
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
