"""Analyst Ratings Collector – daily upgrades/downgrades via yfinance.

Collects individual analyst firm-level rating changes (upgrades, downgrades,
initiations, reiterations) for all active tickers in the universe.

Strategy:
  1. Load active tickers from universe table
  2. Fetch ticker.upgrades_downgrades via YFinanceClient
  3. Filter to lookback_days window
  4. Store with ON CONFLICT DO NOTHING (dedup via unique constraint)

Schedule: Daily 01:00 MEZ (night slot after all other daily collectors)
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.yfinance_client import YFinanceClient
from trading_signals.db.models.fundamentals import AnalystRating
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


class AnalystRatingsCollector(BaseCollector):
    """Collects analyst upgrades/downgrades from yfinance."""

    name = "analyst_ratings_collector"

    def __init__(
        self,
        lookback_days: int = 30,
        batch_size: int = 50,
        delay_between_tickers: float = 0.5,
        delay_between_batches: float = 3.0,
    ) -> None:
        """Initialize with lookback window and rate-limiting params.

        Args:
            lookback_days: Only fetch ratings from the last N days.
            batch_size: Number of tickers per batch.
            delay_between_tickers: Seconds between individual ticker calls.
            delay_between_batches: Seconds between batches.
        """
        self.lookback_days = lookback_days
        self.client = YFinanceClient(
            batch_size=batch_size,
            delay_between_tickers=delay_between_tickers,
            delay_between_batches=delay_between_batches,
        )

    def fetch(self, session: Session) -> list[dict]:
        """Fetch analyst ratings for all active universe tickers.

        Returns:
            List of dicts with rating data (filtered to lookback window).
        """
        # Load active tickers
        stmt = select(Universe.ticker).where(Universe.is_active.is_(True))
        tickers = [row[0] for row in session.execute(stmt).all()]

        logger.info(
            f"[{self.name}] Fetching analyst ratings for {len(tickers)} "
            f"active tickers (lookback={self.lookback_days}d)"
        )

        return self.client.fetch_analyst_ratings(
            tickers, lookback_days=self.lookback_days
        )

    def store(self, session: Session, data: list[dict]) -> tuple[int, int]:
        """Store analyst ratings with ON CONFLICT DO NOTHING.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        records_written = 0

        for record in data:
            stmt = (
                pg_insert(AnalystRating)
                .values(**record)
                .on_conflict_do_nothing(
                    constraint="uq_analyst_rating_dedup"
                )
            )
            result = session.execute(stmt)
            if result.rowcount > 0:
                records_written += 1

        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"ratings ({records_fetched - records_written} already existed)"
        )
        return records_fetched, records_written
