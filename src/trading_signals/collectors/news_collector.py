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
  - Watermark (M3): the window starts at
    ``min(now - lookback, max(published_at) - overlap)`` but never more than
    ``MAX_CATCHUP_DAYS`` back, so missed runs are caught up automatically
  - Pagination cap ``MAX_PAGES`` per request series; hitting it logs a
    warning and marks the run PARTIAL (no silent truncation)
  - Retry per single page (not per whole pagination)
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors.base import BaseCollector
from trading_signals.config import get_settings
from trading_signals.db.models.news import NewsArticle
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

#: Max pages per symbol batch / global request (100 × 50 = 5000 articles).
MAX_PAGES = 100

#: Overlap subtracted from the newest stored article (re-fetches are deduped).
WATERMARK_OVERLAP = timedelta(hours=1)

#: Catch-up after outages is bounded to this many days.
MAX_CATCHUP_DAYS = 7


class NewsCollectorAlpaca(BaseCollector):
    """Collect financial news from Alpaca News API."""

    name = "news_alpaca"

    def __init__(self, lookback_hours: int = DEFAULT_LOOKBACK_HOURS) -> None:
        """Initialize the Alpaca news collector.

        Args:
            lookback_hours: Minimum hours to look back for news articles.
                            Default 36 provides buffer for overnight runs.
                            Extended automatically via the DB watermark.
        """
        self.lookback_hours = lookback_hours
        settings = get_settings()
        self._headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }

    def _start_time(self, latest: Any, now: datetime) -> datetime:
        """Compute the window start from the DB watermark (see module doc)."""
        start = now - timedelta(hours=self.lookback_hours)
        if isinstance(latest, datetime):
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=UTC)  # stored as UTC
            start = min(start, latest - WATERMARK_OVERLAP)
        return max(start, now - timedelta(days=MAX_CATCHUP_DAYS))

    def fetch(self, session: Session) -> list[dict]:
        """Fetch news articles for all universe tickers + global news.

        Returns:
            List of article dicts, deduplicated by article_id.
        """
        latest = session.execute(select(func.max(NewsArticle.published_at))).scalar()
        release_transaction(session)
        tickers = self.get_active_tickers(session)

        start_time = self._start_time(latest, datetime.now(UTC))
        logger.info(
            f"[{self.name}] Fetching news for {len(tickers)} tickers "
            f"since {start_time:%Y-%m-%d %H:%M}Z "
            f"(lookback={self.lookback_hours}h, watermark={latest})"
        )

        seen_ids: set[str] = set()
        all_articles: list[dict] = []

        def _add(articles: list[dict]) -> None:
            for article in articles:
                aid = str(article.get("id", ""))
                if aid and aid not in seen_ids:
                    seen_ids.add(aid)
                    all_articles.append(article)

        # 1. Fetch ticker-specific news in batches
        total_batches = (len(tickers) + SYMBOL_BATCH_SIZE - 1) // SYMBOL_BATCH_SIZE
        for i in range(0, len(tickers), SYMBOL_BATCH_SIZE):
            batch = tickers[i : i + SYMBOL_BATCH_SIZE]
            batch_num = (i // SYMBOL_BATCH_SIZE) + 1

            logger.info(
                f"[{self.name}] Batch {batch_num}/{total_batches}: "
                f"{len(batch)} symbols"
            )
            _add(self._fetch_paginated(batch, start_time, f"batch {batch_num}"))

        # 2. Fetch global news (no symbol filter)
        logger.info(f"[{self.name}] Fetching global market news...")
        _add(self._fetch_paginated(None, start_time, "global"))

        logger.info(
            f"[{self.name}] Fetched {len(all_articles)} unique articles"
        )
        return all_articles

    def _fetch_paginated(
        self,
        symbols: list[str] | None,
        start: datetime,
        label: str,
        max_pages: int = MAX_PAGES,
    ) -> list[dict]:
        """Fetch all pages for one symbol batch (or global news).

        Each page is requested (and retried) individually and reported via
        record_success/record_error. On a failed page the articles fetched
        so far are kept. Hitting ``max_pages`` logs a warning and marks the
        run PARTIAL.
        """
        articles: list[dict] = []
        page_token: str | None = None
        for _page in range(max_pages):
            try:
                payload = _fetch_news_page(symbols, start, self._headers, page_token)
            except Exception as e:
                self.record_error()
                status = getattr(getattr(e, "response", None), "status_code", None)
                logger.error(
                    f"[{self.name}] {label} page {_page + 1} failed: "
                    f"{type(e).__name__}{f' (HTTP {status})' if status else ''}"
                )
                return articles
            self.record_success()
            articles.extend(payload.get("news", []) or [])
            page_token = payload.get("next_page_token")
            if not page_token:
                return articles

        logger.warning(
            f"[{self.name}] {label}: page cap of {max_pages} pages "
            f"({max_pages * PAGE_LIMIT} articles) reached – older articles "
            f"in the window were not fetched"
        )
        self.mark_partial(f"news page cap hit ({label})")
        return articles

    def store(
        self, session: Session, data: list[dict]
    ) -> tuple[int, int]:
        """Store fetched news articles in the database (multi-row insert).

        Uses ON CONFLICT DO NOTHING on article_id for idempotent inserts.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)

        rows = []
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

            rows.append({
                "article_id": article_id,
                "headline": headline,
                "summary": article.get("summary"),
                "source": article.get("source"),
                "author": article.get("author"),
                "published_at": published_at,
                "article_url": article.get("url"),
                "symbols": symbols if symbols else None,
                "is_global": len(symbols) == 0,
            })

        records_written = self._bulk_insert(
            session, NewsArticle, rows, conflict_cols=["article_id"]
        )
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
    page_token: str | None = None,
) -> dict:
    """Fetch ONE page of news articles from Alpaca (retried individually).

    Args:
        symbols: List of ticker symbols (max 50), or None for global news.
        start: Start time for news lookback.
        headers: Alpaca auth headers.
        page_token: ``next_page_token`` of the previous page, if any.

    Returns:
        The JSON payload (``news`` list + ``next_page_token``).
    """
    params: dict[str, Any] = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": PAGE_LIMIT,
        "sort": "desc",
    }
    if symbols:
        params["symbols"] = ",".join(symbols)
    if page_token:
        params["page_token"] = page_token

    response = requests.get(
        NEWS_BASE_URL,
        headers=headers,
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse Alpaca timestamp (ISO 8601) to datetime."""
    if not ts:
        return None
    try:
        # Alpaca returns: "2026-05-12T14:30:00Z"
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
