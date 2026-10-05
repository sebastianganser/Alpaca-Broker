"""Tests for scheduler.runner (run_logged_job, catch-up, alert listener)."""

import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from trading_signals.scheduler import runner
from trading_signals.scheduler.runner import (
    JobOutcome,
    previous_fire_time,
    run_logged_job,
    schedule_startup_catchup,
)
from trading_signals.utils import job_status

BERLIN = ZoneInfo("Europe/Berlin")


def _patch_db():
    return (
        patch.object(runner, "_create_log", return_value=42),
        patch.object(runner, "write_log"),
        patch.object(runner, "notify_run_status"),
    )


class TestRunLoggedJob:
    def test_int_result_is_success_and_logged(self):
        c, w, n = _patch_db()
        with c as create, w as write, n as notify:
            out = run_logged_job("job_a", "coll_a", lambda: 7)
        assert out.status == job_status.SUCCESS
        assert out.records_written == 7
        create.assert_called_once()
        # finalizes the RUNNING row created up front
        assert write.call_args.args[0] == "coll_a"
        assert write.call_args.args[4] == 42
        notify.assert_called_once_with("job_a", job_status.SUCCESS, None)

    def test_exception_becomes_failed_with_alert(self):
        def boom():
            raise RuntimeError("kaputt")

        c, w, n = _patch_db()
        with c, w as write, n as notify:
            out = run_logged_job("job_b", "coll_b", boom)
        assert out.status == job_status.FAILED
        assert "kaputt" in out.notes
        assert write.call_args.args[2].status == job_status.FAILED
        notify.assert_called_once()
        assert notify.call_args.args[1] == job_status.FAILED

    def test_outcome_status_normalized_and_alert_suppressed(self):
        c, w, n = _patch_db()
        with c, w, n as notify:
            out = run_logged_job(
                "job_c",
                "coll_c",
                lambda: JobOutcome(status="completed", notes="x"),  # legacy
                alert=False,
            )
        assert out.status == job_status.SUCCESS
        notify.assert_not_called()

    def test_concurrent_run_of_same_collector_is_skipped(self):
        started = threading.Event()
        release = threading.Event()

        def slow():
            started.set()
            release.wait(5)
            return 1

        c, w, n = _patch_db()
        with c, w as write, n:
            t = threading.Thread(target=run_logged_job, args=("j", "coll_lock", slow))
            t.start()
            assert started.wait(5)
            out = run_logged_job("j__manual", "coll_lock", lambda: 1)
            release.set()
            t.join(5)
        assert out.status == job_status.SKIPPED
        skipped_writes = [
            call
            for call in write.call_args_list
            if call.args[2].status == job_status.SKIPPED
        ]
        assert len(skipped_writes) == 1


class TestPreviousFireTime:
    def test_daily_trigger(self):
        trig = CronTrigger(hour=22, minute=30, timezone=BERLIN)
        now = datetime(2026, 10, 6, 8, 0, tzinfo=BERLIN)
        assert previous_fire_time(trig, now) == datetime(
            2026, 10, 5, 22, 30, tzinfo=BERLIN
        )

    def test_exact_fire_time_counts(self):
        trig = CronTrigger(hour=4, minute=30, timezone=BERLIN)
        now = datetime(2026, 10, 6, 4, 30, tzinfo=BERLIN)
        assert previous_fire_time(trig, now) == now


class TestStartupCatchup:
    def _scheduler(self, job_id, trigger, next_run):
        job = MagicMock(trigger=trigger, next_run_time=next_run)
        sched = MagicMock()
        sched.get_job.side_effect = lambda jid: job if jid == job_id else None
        return sched

    def test_missed_run_is_scheduled_with_delay(self):
        now = datetime(2026, 10, 6, 8, 0, tzinfo=BERLIN)
        trig = CronTrigger(hour=22, minute=30, timezone=BERLIN)
        sched = self._scheduler("price_collector", trig, now + timedelta(hours=14))
        with patch.object(runner, "has_run_since", return_value=False) as hrs:
            out = schedule_startup_catchup(sched, now=now)
        assert out == ["price_collector"]
        assert hrs.call_args.args[0] == "prices_alpaca"  # mapped collector name
        sched.modify_job.assert_called_once_with(
            "price_collector", next_run_time=now + timedelta(minutes=2)
        )

    def test_already_ran_is_not_rescheduled(self):
        now = datetime(2026, 10, 6, 8, 0, tzinfo=BERLIN)
        trig = CronTrigger(hour=22, minute=30, timezone=BERLIN)
        sched = self._scheduler("price_collector", trig, now + timedelta(hours=14))
        with patch.object(runner, "has_run_since", return_value=True):
            assert schedule_startup_catchup(sched, now=now) == []
        sched.modify_job.assert_not_called()

    def test_imminent_regular_run_wins(self):
        now = datetime(2026, 10, 6, 22, 0, tzinfo=BERLIN)
        trig = CronTrigger(hour=22, minute=30, timezone=BERLIN)
        sched = self._scheduler("price_collector", trig, now + timedelta(minutes=30))
        with patch.object(runner, "has_run_since", return_value=False) as hrs:
            assert schedule_startup_catchup(sched, now=now) == []
        hrs.assert_not_called()

    def test_db_error_does_not_raise(self):
        now = datetime(2026, 10, 6, 8, 0, tzinfo=BERLIN)
        trig = CronTrigger(hour=22, minute=30, timezone=BERLIN)
        sched = self._scheduler("price_collector", trig, now + timedelta(hours=14))
        with patch.object(runner, "has_run_since", side_effect=OSError("db down")):
            assert schedule_startup_catchup(sched, now=now) == []


class TestAlertListener:
    def test_error_event_alerts_with_normalized_id(self):
        from apscheduler.events import EVENT_JOB_ERROR

        event = MagicMock(
            code=EVENT_JOB_ERROR,
            job_id="feature_pipeline__manual",
            exception=ValueError("x"),
        )
        with patch.object(runner, "notify_run_status") as notify:
            runner.on_job_event(event)
        assert notify.call_args.args[:2] == ("feature_pipeline", job_status.FAILED)

    def test_missed_event_alerts(self):
        from apscheduler.events import EVENT_JOB_MISSED

        event = MagicMock(
            code=EVENT_JOB_MISSED, job_id="price_collector", scheduled_run_time="t"
        )
        with patch.object(runner, "notify_run_status") as notify:
            runner.on_job_event(event)
        assert "missed" in notify.call_args.args[2]
