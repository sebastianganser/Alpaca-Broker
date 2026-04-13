"""Background task management for long-running operations.

Manages backfill operations that run in background threads
with progress tracking accessible via API.
"""

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


class TaskStatus(StrEnum):
    """Status of a background task."""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
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


class BackfillManager:
    """Manages long-running backfill operations in background threads.

    Provides start/stop semantics and progress tracking for:
    - Price backfill (historical prices from Alpaca)
    - Technical indicator backfill (recompute from prices)
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
            return any(
                t.operation == operation and t.status == TaskStatus.RUNNING
                for t in self._tasks.values()
            )

    def start_price_backfill(self, start_date: str = "2021-01-01") -> str:
        """Start price backfill in a background thread.

        Args:
            start_date: Start date for backfill (ISO format).

        Returns:
            task_id for tracking progress.

        Raises:
            RuntimeError: If a price backfill is already running.
        """
        if self.is_operation_running("price_backfill"):
            raise RuntimeError("Price backfill is already running")

        task_id = f"bf_price_{uuid.uuid4().hex[:8]}"
        task = BackfillTask(task_id=task_id, operation="price_backfill")

        with self._lock:
            self._tasks[task_id] = task

        thread = threading.Thread(
            target=self._run_price_backfill,
            args=(task_id, start_date),
            daemon=True,
        )
        thread.start()
        return task_id

    def start_indicator_backfill(self) -> str:
        """Start technical indicator backfill in a background thread.

        Returns:
            task_id for tracking progress.
        """
        if self.is_operation_running("indicator_backfill"):
            raise RuntimeError("Indicator backfill is already running")

        task_id = f"bf_ta_{uuid.uuid4().hex[:8]}"
        task = BackfillTask(task_id=task_id, operation="indicator_backfill")

        with self._lock:
            self._tasks[task_id] = task

        thread = threading.Thread(
            target=self._run_indicator_backfill,
            args=(task_id,),
            daemon=True,
        )
        thread.start()
        return task_id

    def _run_price_backfill(self, task_id: str, start_date: str) -> None:
        """Execute price backfill in background thread."""
        task = self._tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(UTC)

        try:
            from trading_signals.collectors.prices_alpaca import PriceCollectorAlpaca
            from trading_signals.db.models import Universe
            from trading_signals.db.session import get_session

            with get_session() as session:
                tickers = (
                    session.query(Universe.ticker)
                    .filter(Universe.is_active.is_(True))
                    .all()
                )
                ticker_list = [t[0] for t in tickers]
                task.total_items = len(ticker_list)

            # Calculate lookback days from start_date
            from datetime import date as date_type
            start = date_type.fromisoformat(start_date)
            lookback = (date_type.today() - start).days

            logger.info(
                f"[Backfill {task_id}] Starting price backfill: "
                f"{len(ticker_list)} tickers, {lookback} days lookback"
            )

            collector = PriceCollectorAlpaca(lookback_days=lookback)
            start_time = time.time()

            # The collector processes all tickers in batches
            log = collector.run()

            task.progress_pct = 100.0
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(UTC)

            logger.info(
                f"[Backfill {task_id}] Price backfill completed: "
                f"{log.records_written} records in "
                f"{time.time() - start_time:.0f}s"
            )

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now(UTC)
            logger.error(f"[Backfill {task_id}] Price backfill failed: {e}")

    def _run_indicator_backfill(self, task_id: str) -> None:
        """Execute technical indicator backfill in background thread."""
        task = self._tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(UTC)

        try:
            from trading_signals.db.session import get_session
            from trading_signals.derived.technical_indicators import (
                TechnicalIndicatorsComputer,
            )

            logger.info(f"[Backfill {task_id}] Starting TA indicator backfill")
            start_time = time.time()

            with get_session() as session:
                computer = TechnicalIndicatorsComputer(session)
                written = computer.compute_all(backfill=True)

            task.progress_pct = 100.0
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(UTC)

            logger.info(
                f"[Backfill {task_id}] TA backfill completed: "
                f"{written} records in {time.time() - start_time:.0f}s"
            )

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now(UTC)
            logger.error(f"[Backfill {task_id}] TA backfill failed: {e}")


# Singleton instance
backfill_manager = BackfillManager()
