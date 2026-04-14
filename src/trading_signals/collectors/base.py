"""Abstract base class for all data collectors.

Implements the Template Method pattern:
  run() → check_and_repair_gaps() → fetch() → store() → log()

Each collector run is wrapped in its own session/transaction
and produces exactly one CollectionLog entry.

The log entry is managed in SEPARATE transactions from the
data operations, ensuring that start/finish/error status is
always persisted regardless of whether data collection succeeds.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from trading_signals.collectors.gap_detector import GapRepairResult
from trading_signals.db.models.collection_log import CollectionLog
from trading_signals.db.session import get_session
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


class BaseCollector(ABC):
    """Abstract base for all data collectors."""

    name: str = "unnamed_collector"

    def run(self) -> CollectionLog:
        """Execute the full collector pipeline.

        Template method that orchestrates:
          1. Create log entry (committed immediately)
          2. Check and repair gaps (optional, override in subclass)
          3. Fetch new data from source
          4. Store data in database
          5. Finalize log entry with results (separate commit)

        The log entry is managed in separate transactions from the
        data to ensure start/finish status is always persisted.
        """
        logger.info(f"[{self.name}] Starting collector run...")

        # Step 0: Create and commit log entry immediately
        # so it appears in the UI right away with started_at
        with get_session() as session:
            log = CollectionLog(
                collector_name=self.name,
                started_at=datetime.now(),
            )
            session.add(log)
            session.flush()
            log_id = log.id

        # Step 1-3: Run the actual data collection
        records_fetched = 0
        records_written = 0
        gap_result = None
        status = "success"
        errors_dict = None
        notes = None

        try:
            with get_session() as session:
                # Step 1: Gap detection & repair
                gap_result = self.check_and_repair_gaps(session)

                # Step 2: Fetch new data
                raw_data = self.fetch(session)
                notes = f"fetch returned {len(raw_data)} items"

                # Step 3: Store fetched data
                records_fetched, records_written = self.store(session, raw_data)

        except Exception as e:
            status = "failed"
            errors_dict = {"error": str(e), "type": type(e).__name__}
            notes = f"exception: {type(e).__name__}: {str(e)[:200]}"
            logger.error(f"[{self.name}] Failed: {e}")

        # Step 4: Finalize log entry (always runs, separate transaction)
        # This ensures status/finished_at is persisted even on failure
        finished_at = datetime.now()
        duration = 0.0
        with get_session() as session:
            log = session.get(CollectionLog, log_id)
            if log:
                log.finished_at = finished_at
                log.status = status
                log.records_fetched = records_fetched
                log.records_written = records_written
                log.errors = errors_dict
                log.notes = notes

                if gap_result:
                    log.gaps_detected = gap_result.gaps_detected
                    log.gaps_repaired = gap_result.gaps_repaired
                    log.gaps_extrapolated = gap_result.gaps_extrapolated

                duration = (finished_at - log.started_at).total_seconds()
            # NOTE: Do NOT expunge before commit!
            # get_session() commits on exit – expunge would prevent that.

        # Re-fetch the committed log for return value
        with get_session() as session:
            log = session.get(CollectionLog, log_id)
            if log:
                session.expunge(log)

        if status == "success":
            logger.info(
                f"[{self.name}] Completed in {duration:.1f}s. "
                f"Fetched: {records_fetched}, Written: {records_written}"
            )
            if gap_result and gap_result.gaps_detected > 0:
                logger.info(
                    f"[{self.name}] Gaps: {gap_result.gaps_detected} detected, "
                    f"{gap_result.gaps_repaired} repaired, "
                    f"{gap_result.gaps_extrapolated} extrapolated"
                )
        else:
            logger.info(
                f"[{self.name}] Failed after {duration:.1f}s. "
                f"Error: {errors_dict}"
            )

        return log

    def check_and_repair_gaps(self, session) -> GapRepairResult | None:
        """Override in subclass to enable gap detection.

        Default: no gap checking. Price collectors will override this
        to use GapDetector with their specific fetch function.
        """
        return None

    @abstractmethod
    def fetch(self, session) -> Any:
        """Fetch data from the external source.

        Returns raw data (e.g., a DataFrame or list of dicts).
        """
        ...

    @abstractmethod
    def store(self, session, data: Any) -> tuple[int, int]:
        """Store fetched data in the database.

        Returns:
            Tuple of (records_fetched, records_written).
            records_written <= records_fetched due to ON CONFLICT DO NOTHING.
        """
        ...
