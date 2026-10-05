"""Analyst Ratings Collector – daily upgrades/downgrades via yfinance.

Collects individual analyst firm-level rating changes (upgrades, downgrades,
initiations, reiterations) for all active tickers in the universe.

Strategy:
  1. Skip if a previous successful run already covered the last completed
     NYSE session (weekend/holiday runs, H3/M9)
  2. Load active tickers from universe table
  3. Fetch ticker.upgrades_downgrades via YFinanceClient
  4. Filter to lookback_days window
  5. Store with ON CONFLICT DO NOTHING (dedup via unique constraint)

Schedule: Daily 01:00 MEZ (night slot after all other daily collectors)
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_signals.collectors._db import release_transaction
from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.yfinance_client import YFinanceClient
from trading_signals.db.models.collection_log import CollectionLog
from trading_signals.db.models.fundamentals import AnalystRating
from trading_signals.utils import job_status, market_calendar
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

    def _session_already_covered(self, session: Session) -> bool:
        """True if a successful/partial earlier run already saw the session.

        A run "covers" session X if X was the last completed NYSE session
        when it started. ``collection_log.started_at`` is naive local
        (container) time; ``astimezone()`` interprets it as such.
        """
        target = market_calendar.last_completed_session()
        last_start = session.execute(
            select(func.max(CollectionLog.started_at)).where(
                CollectionLog.collector_name == self.name,
                CollectionLog.status.in_([job_status.SUCCESS, job_status.PARTIAL]),
            )
        ).scalar()
        release_transaction(session)
        if not isinstance(last_start, datetime):
            return False
        try:
            covered = market_calendar.last_completed_session(last_start.astimezone())
        except Exception:
            return False
        if covered >= target:
            logger.info(
                f"[{self.name}] Session {target} already covered by run at "
                f"{last_start:%Y-%m-%d %H:%M} — skipping"
            )
            return True
        return False

    def fetch(self, session: Session) -> list[dict]:
        """Fetch analyst ratings for all active universe tickers.

        Returns:
            List of dicts with rating data (filtered to lookback window).
        """
        if self._session_already_covered(session):
            return []

        tickers = self.get_active_tickers(session)

        logger.info(
            f"[{self.name}] Fetching analyst ratings for {len(tickers)} "
            f"active tickers (lookback={self.lookback_days}d)"
        )

        return self.client.fetch_analyst_ratings(
            tickers,
            lookback_days=self.lookback_days,
            on_success=self.record_success,
            on_error=self.record_error,
        )

    def store(self, session: Session, data: list[dict]) -> tuple[int, int]:
        """Store analyst ratings with ON CONFLICT DO NOTHING (multi-row).

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = len(data)
        records_written = self._bulk_insert(
            session,
            AnalystRating,
            data,
            conflict_cols=["ticker", "firm", "rating_date", "action"],
        )
        session.flush()

        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} "
            f"ratings ({records_fetched - records_written} already existed)"
        )
        return records_fetched, records_written
