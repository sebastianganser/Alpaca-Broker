"""Abstract base class for all data collectors.

Implements the Template Method pattern:
  run() → check_and_repair_gaps() → fetch() → store() → log()

Each collector run produces exactly one CollectionLog entry.

The log entry is managed in SEPARATE transactions from the
data operations, ensuring that start/finish/error status is
always persisted regardless of whether data collection succeeds.

Transaction layout (H5): gap repair, fetch and store each get their own
short session. ``fetch`` implementations should read what they need from
the DB first (e.g. via :meth:`BaseCollector.get_active_tickers`, which
ends the read transaction) and then do the long HTTP work without an open
transaction. ``store`` runs in a fresh session that is committed at the
end.

Run status (H5): subclasses report per-request outcomes via
:meth:`record_success` / :meth:`record_error`. The final status is
``failed`` if every attempt failed (or errors occurred and nothing was
fetched), ``partial`` if more than ``PARTIAL_ERROR_SHARE`` of the attempts
failed or :meth:`mark_partial` was called, otherwise ``success``.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from trading_signals.collectors._db import active_tickers, bulk_insert
from trading_signals.collectors.gap_detector import GapRepairResult
from trading_signals.db.models.collection_log import CollectionLog
from trading_signals.db.session import get_session
from trading_signals.utils import job_status
from trading_signals.utils.alerting import notify_run_status
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

#: Error share above which a run is reported as PARTIAL.
PARTIAL_ERROR_SHARE = 0.10


class BaseCollector(ABC):
    """Abstract base for all data collectors."""

    name: str = "unnamed_collector"

    # Per-run counters (reset in run(); class defaults allow direct
    # fetch()/store() calls in tests without run()).
    _attempts: int = 0
    _errors: int = 0
    _partial_reasons: tuple[str, ...] = ()

    # ── Run outcome tracking ─────────────────────────────────────────
    def _reset_run_state(self) -> None:
        self._attempts = 0
        self._errors = 0
        self._partial_reasons = ()

    def record_success(self, n: int = 1) -> None:
        """Count ``n`` successful source requests."""
        self._attempts += n

    def record_error(self, n: int = 1) -> None:
        """Count ``n`` failed source requests."""
        self._attempts += n
        self._errors += n

    def mark_partial(self, reason: str) -> None:
        """Force at least PARTIAL status for this run (e.g. feed fallback)."""
        self._partial_reasons = (*self._partial_reasons, reason)

    def _compute_status(self, fetched_items: int) -> tuple[str, str | None]:
        """Derive the run status from the counters. Returns (status, note)."""
        attempts, errors = self._attempts, self._errors
        note = None
        if attempts > 0 and errors > 0:
            note = f"{errors}/{attempts} requests failed"
        if attempts > 0 and errors >= attempts:
            return job_status.FAILED, note
        if errors > 0 and fetched_items == 0:
            return job_status.FAILED, note
        if attempts > 0 and errors / attempts > PARTIAL_ERROR_SHARE:
            return job_status.PARTIAL, note
        if self._partial_reasons:
            reasons = "; ".join(self._partial_reasons)
            return job_status.PARTIAL, f"{note}; {reasons}" if note else reasons
        return job_status.SUCCESS, note

    # ── Shared helpers for subclasses ────────────────────────────────
    @staticmethod
    def get_active_tickers(session) -> list[str]:
        """Active universe tickers; ends the read transaction afterwards."""
        return active_tickers(session)

    @staticmethod
    def _bulk_insert(
        session,
        model: Any,
        rows: Iterable[dict],
        conflict_cols: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> int:
        """Multi-row INSERT … ON CONFLICT helper (see ``_db.bulk_insert``)."""
        return bulk_insert(session, model, rows, conflict_cols, **kwargs)

    # ── Template method ──────────────────────────────────────────────
    def run(self) -> CollectionLog:
        """Execute the full collector pipeline.

        Template method that orchestrates:
          1. Create log entry (committed immediately)
          2. Attach log capture handler (captures WARNING+ and related INFO)
          3. Check and repair gaps (optional, override in subclass)
          4. Fetch new data from source
          5. Store data in database
          6. Finalize log entry with results + captured log lines (separate commit)

        The log entry is managed in separate transactions from the
        data to ensure start/finish status is always persisted.
        """
        from trading_signals.utils.logging import CollectorLogCapture

        logger.info(f"[{self.name}] Starting collector run...")
        self._reset_run_state()

        # Step 0: Create and commit log entry immediately
        # so it appears in the UI right away with started_at
        with get_session() as session:
            log = CollectionLog(
                collector_name=self.name,
                started_at=datetime.now(),
                status=job_status.RUNNING,
            )
            session.add(log)
            session.flush()
            log_id = log.id

        # Step 1-3: Run the actual data collection with log capture
        records_fetched = 0
        records_written = 0
        gap_result = None
        status = job_status.SUCCESS
        errors_dict = None
        notes = None
        captured_lines: list[dict] = []

        with CollectorLogCapture(self.name) as capture:
            try:
                # Step 1: Gap detection & repair (own short transaction)
                with get_session() as session:
                    gap_result = self.check_and_repair_gaps(session)

                # Step 2: Fetch new data. Implementations release the read
                # transaction before HTTP work (get_active_tickers).
                with get_session() as session:
                    raw_data = self.fetch(session)
                fetched_items = len(raw_data) if raw_data is not None else 0
                notes = f"fetch returned {fetched_items} items"

                # Step 3: Store fetched data in a fresh transaction
                with get_session() as session:
                    records_fetched, records_written = self.store(session, raw_data)

                status, status_note = self._compute_status(fetched_items)
                if status_note:
                    notes = f"{notes}; {status_note}"

            except Exception as e:
                status = job_status.FAILED
                errors_dict = {"error": str(e), "type": type(e).__name__}
                notes = f"exception: {type(e).__name__}: {str(e)[:200]}"
                logger.error(f"[{self.name}] Failed: {e}")

            captured_lines = capture.get_lines()

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
                log.log_lines = captured_lines if captured_lines else None

                if gap_result:
                    log.gaps_detected = gap_result.gaps_detected
                    log.gaps_repaired = gap_result.gaps_repaired
                    log.gaps_extrapolated = gap_result.gaps_extrapolated

                duration = (finished_at - log.started_at).total_seconds()
            # NOTE: Do NOT expunge before commit!
            # get_session() commits on exit – expunge would prevent that.

        # Push alert for failed/partial runs (never raises)
        notify_run_status(self.name, status, notes)

        # Re-fetch the committed log for return value
        with get_session() as session:
            log = session.get(CollectionLog, log_id)
            if log:
                session.expunge(log)

        if status != job_status.FAILED:
            logger.info(
                f"[{self.name}] Completed ({status}) in {duration:.1f}s. "
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
                f"Error: {errors_dict or notes}"
            )

        if captured_lines:
            warn_count = sum(
                1
                for line in captured_lines
                if line["level"] in ("WARNING", "ERROR", "CRITICAL")
            )
            logger.info(
                f"[{self.name}] Captured {len(captured_lines)} log lines "
                f"({warn_count} warnings/errors)"
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
        Should call :meth:`get_active_tickers` (or ``session.commit()``)
        after DB reads so no transaction idles during HTTP work, and report
        request outcomes via :meth:`record_success` / :meth:`record_error`.
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
