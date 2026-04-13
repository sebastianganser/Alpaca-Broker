"""Job execution tracker.

Uses APScheduler event listeners to maintain a set of currently
running job IDs. This enables the UI to show real-time "LÄUFT"
status for scheduler jobs on both the Dashboard and Settings pages.
"""

import threading
from datetime import datetime

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_SUBMITTED,
)

from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


class JobTracker:
    """Thread-safe tracker for currently running scheduler jobs."""

    def __init__(self) -> None:
        self._running: dict[str, datetime] = {}  # job_id -> started_at
        self._lock = threading.Lock()

    def on_job_submitted(self, event) -> None:
        """Called when a job is submitted for execution."""
        with self._lock:
            self._running[event.job_id] = datetime.now()
        logger.debug(f"[JobTracker] Job started: {event.job_id}")

    def on_job_finished(self, event) -> None:
        """Called when a job finishes (success or error)."""
        with self._lock:
            self._running.pop(event.job_id, None)
        logger.debug(f"[JobTracker] Job finished: {event.job_id}")

    def is_running(self, job_id: str) -> bool:
        """Check if a specific job is currently running."""
        with self._lock:
            return job_id in self._running

    def get_running_jobs(self) -> dict[str, datetime]:
        """Get all currently running jobs with their start times."""
        with self._lock:
            return dict(self._running)

    def register(self, scheduler) -> None:
        """Register event listeners with the scheduler."""
        scheduler.add_listener(self.on_job_submitted, EVENT_JOB_SUBMITTED)
        scheduler.add_listener(
            self.on_job_finished, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
        )
        logger.info("[JobTracker] Registered APScheduler event listeners")


# Singleton instance
job_tracker = JobTracker()
