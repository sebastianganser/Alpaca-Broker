"""Shared job execution helpers for scheduler jobs.

* :func:`run_logged_job` – the one place that writes ``collection_log`` rows
  for non-collector jobs (derived computations, retention, chain), captures
  log lines, uses :mod:`trading_signals.utils.job_status` constants and sends
  alerts for FAILED/PARTIAL runs.
* :func:`register_alert_listeners` – alerts for uncaught job exceptions and
  missed runs (APScheduler events).
* :func:`mark_stale_runs` – mark RUNNING rows left behind by a crash/restart.
* :func:`schedule_startup_catchup` – re-run critical jobs whose last
  scheduled run was missed while the container was down.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from trading_signals.db.base import now_local
from trading_signals.scheduler.registry import (
    CATCHUP_JOBS,
    get_collector_name,
    normalize_job_id,
)
from trading_signals.utils import job_status
from trading_signals.utils.alerting import notify_run_status
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

#: Rows still RUNNING after this long are considered orphaned.
STALE_RUN_AGE = timedelta(hours=6)
STALE_NOTE = "stale: process restart"


@dataclass
class JobOutcome:
    """Result of a job body executed by :func:`run_logged_job`."""

    status: str = job_status.SUCCESS
    records_written: int = 0
    records_fetched: int | None = None
    notes: str | None = None


# One lock per collector_name: prevents a manual trigger and the nightly
# chain from running the same step concurrently (different job ids, so
# APScheduler's max_instances can't catch it).
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(name: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(name, threading.Lock())


def _coerce_outcome(result: object) -> JobOutcome:
    if isinstance(result, JobOutcome):
        result.status = job_status.normalize(result.status) or job_status.SUCCESS
        return result
    if isinstance(result, int) and not isinstance(result, bool):
        return JobOutcome(records_written=result)
    return JobOutcome()


def _create_log(collector_name: str, started_at: datetime) -> int | None:
    """Insert a RUNNING row so the UI shows the run and crashes are detectable."""
    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session

    try:
        with get_session() as session:
            entry = CollectionLog(
                collector_name=collector_name,
                started_at=started_at,
                status=job_status.RUNNING,
                gaps_detected=0,
            )
            session.add(entry)
            session.flush()
            return entry.id
    except Exception as e:  # DB down → the job body will fail and report
        logger.error(f"[{collector_name}] could not create collection_log row: {e}")
        return None


def write_log(
    collector_name: str,
    started_at: datetime,
    outcome: JobOutcome,
    log_lines: list | None = None,
    log_id: int | None = None,
) -> None:
    """Finalize (or insert) the collection_log row for a run. Never raises."""
    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session

    try:
        with get_session() as session:
            entry = session.get(CollectionLog, log_id) if log_id else None
            if entry is None:
                entry = CollectionLog(
                    collector_name=collector_name, started_at=started_at
                )
                session.add(entry)
            entry.finished_at = now_local()
            entry.status = outcome.status
            entry.records_written = outcome.records_written
            entry.records_fetched = (
                outcome.records_fetched
                if outcome.records_fetched is not None
                else outcome.records_written
            )
            entry.gaps_detected = entry.gaps_detected or 0
            entry.notes = outcome.notes[:2000] if outcome.notes else None
            entry.log_lines = log_lines or None
    except Exception as e:
        logger.error(f"[{collector_name}] failed to write collection_log: {e}")


def run_logged_job(
    job_id: str,
    collector_name: str,
    fn: Callable[[], object],
    *,
    alert: bool = True,
) -> JobOutcome:
    """Run ``fn`` with collection_log bookkeeping, log capture and alerting.

    ``fn`` may return ``int`` (records written → SUCCESS), a
    :class:`JobOutcome`, or ``None``. Exceptions are caught and reported as
    FAILED. If the same ``collector_name`` is already running in this process
    the run is recorded as SKIPPED.
    """
    from trading_signals.utils.logging import CollectorLogCapture

    logger.info(f"Scheduler triggered: {job_id} ({collector_name})")
    lock = _lock_for(collector_name)
    started_at = now_local()
    if not lock.acquire(blocking=False):
        outcome = JobOutcome(
            status=job_status.SKIPPED, notes="already running in this process"
        )
        logger.warning(f"[{collector_name}] skipped: already running")
        write_log(collector_name, started_at, outcome)
        return outcome

    try:
        log_id = _create_log(collector_name, started_at)
        with CollectorLogCapture(collector_name) as capture:
            try:
                outcome = _coerce_outcome(fn())
            except Exception as e:
                logger.error(f"[{collector_name}] FAILED: {type(e).__name__}: {e}")
                outcome = JobOutcome(
                    status=job_status.FAILED, notes=f"{type(e).__name__}: {e}"
                )
            lines = capture.get_lines()
        write_log(collector_name, started_at, outcome, lines, log_id)
    finally:
        lock.release()

    logger.info(
        f"[{collector_name}] finished: status={outcome.status}, "
        f"written={outcome.records_written}"
    )
    if alert:
        notify_run_status(job_id, outcome.status, outcome.notes)
    return outcome


# ── Startup / recovery ──────────────────────────────────────────────────


def mark_stale_runs(
    max_age: timedelta = STALE_RUN_AGE, now: datetime | None = None
) -> int:
    """Mark collection_log rows stuck in RUNNING/NULL for > ``max_age`` as FAILED."""
    from sqlalchemy import or_, update

    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session

    now = now or now_local()
    with get_session() as session:
        result = session.execute(
            update(CollectionLog)
            .where(
                or_(
                    CollectionLog.status.is_(None),
                    CollectionLog.status == job_status.RUNNING,
                )
            )
            .where(CollectionLog.started_at < now - max_age)
            .values(status=job_status.FAILED, finished_at=now, notes=STALE_NOTE)
        )
        count = result.rowcount or 0
    if count:
        logger.warning(f"Marked {count} orphaned collection_log run(s) as failed")
    return count


def previous_fire_time(trigger, now: datetime, lookback: timedelta = timedelta(days=8)):
    """Most recent fire time of ``trigger`` at or before ``now`` (or None)."""
    t = trigger.get_next_fire_time(None, now - lookback)
    prev = None
    while t is not None and t <= now:
        prev = t
        t = trigger.get_next_fire_time(t, t + timedelta(seconds=1))
    return prev


#: Statuses that count as "the run happened" for catch-up purposes.
_RAN_STATUSES = (
    job_status.SUCCESS,
    job_status.PARTIAL,
    job_status.SKIPPED,
    *(k for k, v in job_status.LEGACY_MAP.items() if v == job_status.SUCCESS),
)


def has_run_since(collector_name: str, since: datetime) -> bool:
    """True if a finished (success/partial/skipped) run started after ``since``."""
    from sqlalchemy import select

    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session

    with get_session() as session:
        row = session.execute(
            select(CollectionLog.id)
            .where(CollectionLog.collector_name == collector_name)
            .where(CollectionLog.started_at >= since)
            .where(CollectionLog.status.in_(_RAN_STATUSES))
            .limit(1)
        ).first()
    return row is not None


def schedule_startup_catchup(scheduler, now: datetime | None = None) -> list[str]:
    """Re-run critical jobs once if their last scheduled run was missed.

    Returns the list of job ids scheduled for catch-up.
    """
    now = now or now_local()
    scheduled: list[str] = []
    for job_id, delay_min in CATCHUP_JOBS.items():
        job = scheduler.get_job(job_id)
        if job is None:
            continue
        prev = previous_fire_time(job.trigger, now)
        if prev is None:
            continue
        # Regular run imminent anyway → nothing to do
        if job.next_run_time and job.next_run_time <= now + timedelta(hours=1):
            continue
        try:
            if has_run_since(get_collector_name(job_id), prev - timedelta(minutes=1)):
                continue
        except Exception as e:
            logger.error(f"Catch-up check for {job_id} failed: {e}")
            continue
        run_at = now + timedelta(minutes=delay_min)
        scheduler.modify_job(job_id, next_run_time=run_at)
        scheduled.append(job_id)
        logger.warning(
            f"Catch-up: {job_id} missed its run at {prev:%Y-%m-%d %H:%M} → "
            f"running once at {run_at:%H:%M}"
        )
    return scheduled


def on_job_event(event) -> None:
    """APScheduler listener: alert on uncaught exceptions and missed runs."""
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

    job_id = normalize_job_id(event.job_id)
    if event.code == EVENT_JOB_ERROR:
        exc = getattr(event, "exception", None)
        notify_run_status(
            job_id, job_status.FAILED, f"uncaught {type(exc).__name__}: {exc}"
        )
    elif event.code == EVENT_JOB_MISSED:
        notify_run_status(
            job_id,
            job_status.FAILED,
            f"missed scheduled run at {event.scheduled_run_time}",
        )


def register_alert_listeners(scheduler) -> None:
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

    scheduler.add_listener(on_job_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
