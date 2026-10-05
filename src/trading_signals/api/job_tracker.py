"""Job execution tracker.

Uses APScheduler event listeners to maintain a set of currently
running job IDs. This enables the UI to show real-time "LÄUFT"
status for scheduler jobs on both the Dashboard and Settings pages.

One-shot manual clones of chain steps (``<job_id>__manual``) are tracked
under their base job id.
"""

import threading
from datetime import datetime

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_SUBMITTED,
)

from trading_signals.scheduler.registry import normalize_job_id
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


class JobTracker:
    """Thread-safe tracker for currently running scheduler jobs."""

    def __init__(self) -> None:
        self._running: dict[str, int] = {}  # job_id -> running instance count
        self._started: dict[str, datetime] = {}  # job_id -> started_at
        self._lock = threading.Lock()

    def on_job_submitted(self, event) -> None:
        """Called when a job is submitted for execution."""
        job_id = normalize_job_id(event.job_id)
        with self._lock:
            self._running[job_id] = self._running.get(job_id, 0) + 1
            self._started.setdefault(job_id, datetime.now())
        logger.debug(f"[JobTracker] Job started: {job_id}")

    def on_job_finished(self, event) -> None:
        """Called when a job finishes (success, error or missed)."""
        job_id = normalize_job_id(event.job_id)
        with self._lock:
            count = self._running.get(job_id, 0) - 1
            if count > 0:
                self._running[job_id] = count
            else:
                self._running.pop(job_id, None)
                self._started.pop(job_id, None)
        logger.debug(f"[JobTracker] Job finished: {job_id}")

    def is_running(self, job_id: str) -> bool:
        """Check if a specific job is currently running."""
        with self._lock:
            return normalize_job_id(job_id) in self._running

    def get_running_jobs(self) -> dict[str, datetime]:
        """Get all currently running jobs with their start times."""
        with self._lock:
            return {j: self._started.get(j, datetime.now()) for j in self._running}

    def register(self, scheduler) -> None:
        """Register event listeners with the scheduler."""
        scheduler.add_listener(self.on_job_submitted, EVENT_JOB_SUBMITTED)
        scheduler.add_listener(
            self.on_job_finished, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
        )
        logger.info("[JobTracker] Registered APScheduler event listeners")


# Singleton instance
job_tracker = JobTracker()
