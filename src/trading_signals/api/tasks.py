"""Background task management for long-running operations.

Manages backfill operations that run in background threads
with progress tracking accessible via API.

State is in-memory only (lost on restart); finished tasks are pruned so the
registry cannot grow without bound.
"""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

#: Finished tasks kept for the status endpoint (older ones are pruned).
MAX_FINISHED_TASKS = 20
#: Commit indicator backfill work every N tickers.
INDICATOR_COMMIT_EVERY = 50


class TaskStatus(StrEnum):
    """Status of a background task."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"  # finished, but some batches/tickers failed
    FAILED = "failed"


@dataclass
class BackfillTask:
    """Tracks a single backfill operation."""

    task_id: str
    operation: str
    status: TaskStatus = TaskStatus.IDLE
    progress_pct: float = 0.0
    current_ticker: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    eta_seconds: float | None = None
    error: str | None = None
    total_items: int = 0
    processed_items: int = 0


def _status_from_failures(failed: int, total: int) -> TaskStatus:
    if failed == 0:
        return TaskStatus.COMPLETED
    if total and failed >= total:
        return TaskStatus.FAILED
    return TaskStatus.PARTIAL


def resolve_start_date(start_date: str | None) -> date:
    """Parse ``start_date`` (ISO) and clamp it to the retention window.

    Raises:
        ValueError: if ``start_date`` is not a valid ISO date.
    """
    from trading_signals.utils.retention import data_start_date

    floor = data_start_date()
    if not start_date:
        return floor
    parsed = date.fromisoformat(start_date)
    return max(parsed, floor)


def _active_tickers() -> list[str]:
    from sqlalchemy import select

    from trading_signals.db.models.universe import Universe
    from trading_signals.db.session import get_session

    with get_session() as session:
        stmt = (
            select(Universe.ticker)
            .where(Universe.is_active.is_(True))
            .order_by(Universe.ticker)
        )
        return [row[0] for row in session.execute(stmt).all()]


class BackfillManager:
    """Manages long-running backfill operations in background threads.

    Provides start/stop semantics and progress tracking for:
    - Price backfill (historical prices from Alpaca)
    - Technical indicator backfill (recompute from prices)
    - Sector enrichment (yfinance)
    """

    def __init__(self):
        self._tasks: dict[str, BackfillTask] = {}
        self._lock = threading.Lock()

    def get_all_status(self) -> list[BackfillTask]:
        """Get status of all tasks."""
        with self._lock:
            return list(self._tasks.values())

    def get_status(self, task_id: str) -> BackfillTask | None:
        """Get status of a specific task."""
        with self._lock:
            return self._tasks.get(task_id)

    def is_operation_running(self, operation: str) -> bool:
        """Check if a specific operation type is already running."""
        with self._lock:
            return self._is_running_locked(operation)

    def _is_running_locked(self, operation: str) -> bool:
        return any(
            t.operation == operation and t.status == TaskStatus.RUNNING
            for t in self._tasks.values()
        )

    def _prune_locked(self) -> None:
        """Drop the oldest finished tasks beyond ``MAX_FINISHED_TASKS``."""
        finished = [t for t in self._tasks.values() if t.status != TaskStatus.RUNNING]
        if len(finished) <= MAX_FINISHED_TASKS:
            return
        finished.sort(
            key=lambda t: (
                t.completed_at or t.started_at or datetime.min.replace(tzinfo=UTC)
            )
        )
        for t in finished[: len(finished) - MAX_FINISHED_TASKS]:
            self._tasks.pop(t.task_id, None)

    def _start(
        self,
        operation: str,
        prefix: str,
        label: str,
        target: Callable[..., None],
        *args,
    ) -> str:
        """Atomically check-and-register a RUNNING task, then start its thread.

        Raises:
            RuntimeError: If the same operation is already running.
        """
        task_id = f"{prefix}_{uuid.uuid4().hex[:8]}"
        with self._lock:
            if self._is_running_locked(operation):
                raise RuntimeError(f"{label} is already running")
            self._prune_locked()
            self._tasks[task_id] = BackfillTask(
                task_id=task_id,
                operation=operation,
                status=TaskStatus.RUNNING,
                started_at=datetime.now(UTC),
            )

        thread = threading.Thread(
            target=self._run_guarded,
            args=(task_id, operation, target, args),
            daemon=True,
            name=f"backfill-{task_id}",
        )
        try:
            thread.start()
        except Exception as e:
            self._finish(task_id, TaskStatus.FAILED, f"thread start failed: {e}")
            raise
        return task_id

    def _run_guarded(self, task_id: str, operation: str, target, args) -> None:
        try:
            target(task_id, *args)
        except Exception as e:
            logger.error(f"[Backfill {task_id}] {operation} failed: {e}")
            self._finish(task_id, TaskStatus.FAILED, str(e))
        task = self.get_status(task_id)
        if task is not None and task.status in (TaskStatus.FAILED, TaskStatus.PARTIAL):
            from trading_signals.utils.alerting import notify_run_status

            notify_run_status(operation, task.status.value, task.error)

    def _finish(
        self, task_id: str, status: TaskStatus, error: str | None = None
    ) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = status
            task.error = error
            task.completed_at = datetime.now(UTC)
            task.current_ticker = None
            if status != TaskStatus.FAILED:
                task.progress_pct = 100.0
                task.eta_seconds = 0.0

    def start_price_backfill(self, start_date: str | None = None) -> str:
        """Start price backfill in a background thread.

        Args:
            start_date: Optional start date (ISO). The collector always
                (re-)loads the full retention window from
                ``data_start_date()``; earlier dates are clamped.

        Returns:
            task_id for tracking progress.

        Raises:
            RuntimeError: If a price backfill is already running.
            ValueError: If ``start_date`` is not a valid ISO date.
        """
        start = resolve_start_date(start_date)
        return self._start(
            "price_backfill",
            "bf_price",
            "Price backfill",
            self._run_price_backfill,
            start,
        )

    def start_indicator_backfill(self) -> str:
        """Start technical indicator backfill in a background thread.

        Returns:
            task_id for tracking progress.
        """
        return self._start(
            "indicator_backfill",
            "bf_ta",
            "Indicator backfill",
            self._run_indicator_backfill,
        )

    def start_sector_enrichment(self) -> str:
        """Start sector/industry enrichment in a background thread.

        Fetches sector/industry data from yfinance for all active tickers
        (full reload) and blacklists non-equity tickers.

        Returns:
            task_id for tracking progress.
        """
        return self._start(
            "sector_enrichment",
            "enrich",
            "Sector enrichment",
            self._run_sector_enrichment,
        )

    def _update_progress(
        self,
        task_id: str,
        processed: int,
        total: int,
        current_ticker: str | None = None,
        start_time: float | None = None,
    ) -> None:
        """Thread-safe progress update for a running task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.processed_items = processed
            task.total_items = total
            task.current_ticker = current_ticker
            task.progress_pct = (processed / total * 100) if total > 0 else 0.0

            # ETA calculation
            if start_time and processed > 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0  # items per second
                remaining = total - processed
                task.eta_seconds = remaining / rate if rate > 0 else None

    def _run_price_backfill(self, task_id: str, start: date) -> None:
        """Execute price backfill batch by batch via the collector's upsert path.

        Uses ``PriceCollectorAlpaca.refresh_full_history`` (bulk upsert +
        TA/target recompute), one transaction per batch.
        """
        from trading_signals.collectors.prices_alpaca import (
            BATCH_SIZE,
            PriceCollectorAlpaca,
        )
        from trading_signals.db.session import get_session

        tickers = _active_tickers()
        total_batches = (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE
        logger.info(
            f"[Backfill {task_id}] Starting price backfill: "
            f"{len(tickers)} tickers in {total_batches} batches (from {start})"
        )

        start_time = time.time()
        collector = PriceCollectorAlpaca()
        total_written = 0
        failed_batches: list[str] = []

        for i in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[i : i + BATCH_SIZE]
            batch_num = (i // BATCH_SIZE) + 1
            self._update_progress(
                task_id,
                processed=batch_num - 1,
                total=total_batches,
                current_ticker=(
                    f"Batch {batch_num}/{total_batches} ({batch[0]}...{batch[-1]})"
                ),
                start_time=start_time,
            )
            try:
                with get_session() as session:
                    written = collector.refresh_full_history(session, batch)
                total_written += written or 0
                logger.info(
                    f"[Backfill {task_id}] Batch {batch_num}/{total_batches}: "
                    f"{written} records upserted"
                )
            except Exception as e:
                failed_batches.append(f"{batch[0]}..{batch[-1]}")
                logger.error(f"[Backfill {task_id}] Batch {batch_num} failed: {e}")

        self._update_progress(
            task_id, processed=total_batches, total=total_batches, start_time=start_time
        )
        status = _status_from_failures(len(failed_batches), total_batches)
        error = (
            f"{len(failed_batches)}/{total_batches} batches failed: "
            f"{', '.join(failed_batches[:10])}"
            if failed_batches
            else None
        )
        self._finish(task_id, status, error)
        logger.info(
            f"[Backfill {task_id}] Price backfill {status.value}: "
            f"{total_written} records in {time.time() - start_time:.0f}s"
        )

    def _run_indicator_backfill(self, task_id: str) -> None:
        """Execute technical indicator backfill with per-ticker progress.

        Each ticker runs in its own SAVEPOINT, so a failing ticker is rolled
        back alone instead of aborting the whole transaction; work is
        committed every ``INDICATOR_COMMIT_EVERY`` tickers.
        """
        from trading_signals.db.session import get_session
        from trading_signals.derived.technical_indicators import (
            TechnicalIndicatorsComputer,
        )

        tickers = _active_tickers()
        logger.info(
            f"[Backfill {task_id}] Starting TA indicator backfill: "
            f"{len(tickers)} tickers"
        )

        start_time = time.time()
        total_written = 0
        failed: list[str] = []

        with get_session() as session:
            computer = TechnicalIndicatorsComputer(session)

            # Pre-load SPY prices
            computer._spy_df = computer._load_price_history("SPY")

            for i, ticker in enumerate(tickers, 1):
                self._update_progress(
                    task_id,
                    processed=i - 1,
                    total=len(tickers),
                    current_ticker=ticker,
                    start_time=start_time,
                )
                try:
                    with session.begin_nested():
                        written = computer._compute_backfill(ticker)
                    total_written += written or 0
                except Exception as e:
                    failed.append(ticker)
                    logger.error(f"[Backfill {task_id}] Error for {ticker}: {e}")

                if i % INDICATOR_COMMIT_EVERY == 0:
                    session.commit()
                    logger.info(
                        f"[Backfill {task_id}] Progress: "
                        f"{i}/{len(tickers)} tickers, {total_written} records"
                    )

        self._update_progress(
            task_id, processed=len(tickers), total=len(tickers), start_time=start_time
        )
        status = _status_from_failures(len(failed), len(tickers))
        error = (
            f"{len(failed)}/{len(tickers)} tickers failed: {', '.join(failed[:20])}"
            if failed
            else None
        )
        self._finish(task_id, status, error)
        logger.info(
            f"[Backfill {task_id}] TA backfill {status.value}: "
            f"{total_written} records in {time.time() - start_time:.0f}s "
            f"({len(failed)} errors)"
        )

    def _run_sector_enrichment(self, task_id: str) -> None:
        """Execute sector enrichment (shared implementation) with progress."""
        from trading_signals.scheduler.sector_enrichment import enrich_sectors

        tickers = _active_tickers()
        if not tickers:
            logger.info(f"[Enrichment {task_id}] No active tickers to enrich")
            self._finish(task_id, TaskStatus.COMPLETED)
            return

        logger.info(
            f"[Enrichment {task_id}] Starting full sector reload: "
            f"{len(tickers)} active tickers"
        )
        start_time = time.time()

        def _progress(processed: int, total: int, current: str | None) -> None:
            self._update_progress(task_id, processed, total, current, start_time)

        result = enrich_sectors(tickers, source="manual_enrichment", progress=_progress)
        self._finish(task_id, TaskStatus.COMPLETED)
        logger.info(
            f"[Enrichment {task_id}] Completed: "
            f"{result.enriched}/{len(tickers)} tickers enriched, "
            f"{len(result.deactivated)} blacklisted in "
            f"{time.time() - start_time:.0f}s"
        )


# Singleton instance
backfill_manager = BackfillManager()
