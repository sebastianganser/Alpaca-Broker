"""Abstract base class for all data collectors.

Implements the Template Method pattern:
  run() → check_and_repair_gaps() → fetch() → store() → log()

Each collector run is wrapped in its own session/transaction
and produces exactly one CollectionLog entry.
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
          1. Start log entry
          2. Check and repair gaps (optional, override in subclass)
          3. Fetch new data from source
          4. Store data in database
          5. Finalize log entry with results
        """
        logger.info(f"[{self.name}] Starting collector run...")

        with get_session() as session:
            # Step 0: Create log entry
            log = CollectionLog(
                collector_name=self.name,
                started_at=datetime.now(),
            )
            session.add(log)
            session.flush()  # Get the ID

            try:
                # Step 1: Gap detection & repair
                gap_result = self.check_and_repair_gaps(session)

                # Step 2: Fetch new data
                raw_data = self.fetch(session)

                # Step 3: Store fetched data
                records_fetched, records_written = self.store(session, raw_data)

                # Step 4: Finalize log
                log.finished_at = datetime.now()
                log.status = "success"
                log.records_fetched = records_fetched
                log.records_written = records_written

                if gap_result:
                    log.gaps_detected = gap_result.gaps_detected
                    log.gaps_repaired = gap_result.gaps_repaired
                    log.gaps_extrapolated = gap_result.gaps_extrapolated

                duration = (log.finished_at - log.started_at).total_seconds()
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

            except Exception as e:
                log.finished_at = datetime.now()
                log.status = "failed"
                log.errors = {"error": str(e), "type": type(e).__name__}
                logger.error(f"[{self.name}] Failed: {e}")
                # Ensure all attributes are loaded before detaching
                session.flush()
                session.expunge(log)
                raise

            # Detach log from session so it can be used after close
            session.expunge(log)

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
