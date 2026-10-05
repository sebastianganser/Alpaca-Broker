"""Tests for ops/analysis API routes, auth guard, background tasks, TZDateTime."""

import threading
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from trading_signals.api.deps import (
    get_api_key,
    get_db,
    get_scheduler,
    require_write_access,
)
from trading_signals.api.job_tracker import JobTracker
from trading_signals.api.routes import analysis, dashboard, operations
from trading_signals.api.tasks import (
    MAX_FINISHED_TASKS,
    BackfillManager,
    BackfillTask,
    TaskStatus,
    resolve_start_date,
)
from trading_signals.db.base import TZDateTime

BERLIN = ZoneInfo("Europe/Berlin")
WRITE_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def _app(router, db=None, scheduler=None, api_key=""):
    app = FastAPI()
    app.include_router(
        router, prefix="/api/v1", dependencies=[Depends(require_write_access)]
    )
    app.dependency_overrides[get_db] = lambda: db or MagicMock()
    app.dependency_overrides[get_scheduler] = lambda: scheduler
    app.dependency_overrides[get_api_key] = lambda: api_key
    return TestClient(app)


# ── Auth guard ──────────────────────────────────────────────────────────


class TestWriteAccess:
    def _client(self, api_key=""):
        router = APIRouter()

        @router.get("/thing")
        def read():
            return {"ok": True}

        @router.post("/thing")
        def write():
            return {"ok": True}

        return _app(router, api_key=api_key)

    def test_get_always_allowed(self):
        assert self._client().get("/api/v1/thing").status_code == 200
        assert self._client("secret").get("/api/v1/thing").status_code == 200

    def test_post_without_key_requires_custom_header(self):
        c = self._client()
        assert c.post("/api/v1/thing").status_code == 403
        assert c.post("/api/v1/thing", headers=WRITE_HEADERS).status_code == 200

    def test_post_with_key_configured(self):
        c = self._client("secret")
        assert c.post("/api/v1/thing", headers=WRITE_HEADERS).status_code == 401
        assert (
            c.post("/api/v1/thing", headers={"X-API-Key": "wrong"}).status_code == 401
        )
        assert (
            c.post("/api/v1/thing", headers={"X-API-Key": "secret"}).status_code == 200
        )


# ── Operations ──────────────────────────────────────────────────────────


class TestReset:
    def test_requires_confirmation(self):
        db = MagicMock()
        c = _app(operations.router, db=db)
        r = c.post("/api/v1/ops/db/reset", headers=WRITE_HEADERS)
        assert r.status_code == 400
        db.execute.assert_not_called()

    def test_query_confirmation_truncates_in_one_statement(self):
        db = MagicMock()
        c = _app(operations.router, db=db)
        r = c.post("/api/v1/ops/db/reset?confirm=RESET", headers=WRITE_HEADERS)
        assert r.status_code == 200
        db.execute.assert_called_once()
        sql = str(db.execute.call_args.args[0])
        assert sql.startswith("TRUNCATE TABLE ")
        assert "signals.prices_daily" in sql
        assert "signals.universe," not in sql and not sql.endswith("signals.universe")
        db.commit.assert_called_once()

    def test_body_confirmation(self):
        db = MagicMock()
        c = _app(operations.router, db=db)
        r = c.post(
            "/api/v1/ops/db/reset", json={"confirm": "RESET"}, headers=WRITE_HEADERS
        )
        assert r.status_code == 200

    def test_reset_table_names_from_metadata(self):
        names = operations.reset_table_names()
        for t in (
            "signals.estimates_snapshot",
            "signals.short_interest",
            "signals.short_volume",
            "signals.options_iv_snapshot",
            "signals.collection_log",
            "signals.news_sentiment",
        ):
            assert t in names
        assert "signals.universe" not in names
        assert "signals.ticker_blacklist" not in names
        # dependents first: sentiment before its parent articles
        assert names.index("signals.news_sentiment") < names.index(
            "signals.news_articles"
        )


class TestTrigger:
    def _scheduler(self, next_run):
        job = MagicMock(next_run_time=next_run, func=lambda: None)
        job.name = "Job"
        sched = MagicMock(running=True)
        sched.get_job.return_value = job
        return sched

    def test_paused_chain_step_runs_as_one_shot_clone(self):
        sched = self._scheduler(None)
        c = _app(operations.router, scheduler=sched)
        r = c.post(
            "/api/v1/ops/scheduler/feature_pipeline/trigger", headers=WRITE_HEADERS
        )
        assert r.status_code == 200
        sched.modify_job.assert_not_called()
        kwargs = sched.add_job.call_args.kwargs
        assert kwargs["id"] == "feature_pipeline__manual"
        assert kwargs["trigger"] == "date"

    def test_regular_job_runs_now(self):
        sched = self._scheduler(datetime.now(UTC) + timedelta(hours=3))
        c = _app(operations.router, scheduler=sched)
        r = c.post(
            "/api/v1/ops/scheduler/price_collector/trigger", headers=WRITE_HEADERS
        )
        assert r.status_code == 200
        sched.modify_job.assert_called_once()
        sched.add_job.assert_not_called()

    def test_manual_clones_hidden_from_list(self):
        def job(jid):
            j = MagicMock(id=jid, next_run_time=None, pending=False)
            j.name = jid
            j.trigger = "date"
            return j

        sched = MagicMock(running=True)
        sched.get_jobs.return_value = [
            job("feature_pipeline"),
            job("feature_pipeline__manual"),
        ]
        c = _app(operations.router, scheduler=sched)
        ids = [j["id"] for j in c.get("/api/v1/ops/scheduler").json()]
        assert ids == ["feature_pipeline"]


class TestAnalysisTrigger:
    def test_async_202(self):
        sched = MagicMock(running=True)
        c = _app(analysis.router, scheduler=sched)
        with patch.object(analysis.job_tracker, "is_running", return_value=False):
            r = c.post("/api/v1/analysis/trigger", headers=WRITE_HEADERS)
        assert r.status_code == 202
        assert sched.modify_job.call_args.args[0] == "feature_analysis"

    def test_conflict_when_running(self):
        sched = MagicMock(running=True)
        c = _app(analysis.router, scheduler=sched)
        with patch.object(analysis.job_tracker, "is_running", return_value=True):
            r = c.post("/api/v1/analysis/trigger", headers=WRITE_HEADERS)
        assert r.status_code == 409
        sched.modify_job.assert_not_called()

    def test_no_scheduler_503(self):
        c = _app(analysis.router, scheduler=None)
        r = c.post("/api/v1/analysis/trigger", headers=WRITE_HEADERS)
        assert r.status_code == 503

    def test_list_limit_bounded(self):
        c = _app(analysis.router)
        assert c.get("/api/v1/analysis/list?limit=0").status_code == 422
        assert c.get("/api/v1/analysis/list?limit=1000").status_code == 422


# ── Job tracker ─────────────────────────────────────────────────────────


def test_job_tracker_counts_manual_clone_as_job():
    tracker = JobTracker()
    tracker.on_job_submitted(MagicMock(job_id="feature_pipeline__manual"))
    tracker.on_job_submitted(MagicMock(job_id="feature_pipeline"))
    assert tracker.is_running("feature_pipeline")
    tracker.on_job_finished(MagicMock(job_id="feature_pipeline"))
    assert tracker.is_running("feature_pipeline")  # one instance left
    tracker.on_job_finished(MagicMock(job_id="feature_pipeline__manual"))
    assert not tracker.is_running("feature_pipeline")


# ── Background tasks ────────────────────────────────────────────────────


class TestBackfillManager:
    def test_second_start_raises_while_running(self):
        mgr = BackfillManager()
        started, release = threading.Event(), threading.Event()

        def target(task_id):
            started.set()
            release.wait(5)
            mgr._finish(task_id, TaskStatus.COMPLETED)

        tid = mgr._start("op", "p", "Op", target)
        assert started.wait(5)
        with pytest.raises(RuntimeError):
            mgr._start("op", "p", "Op", target)
        release.set()
        for _ in range(100):
            if mgr.get_status(tid).status != TaskStatus.RUNNING:
                break
            threading.Event().wait(0.02)
        assert mgr.get_status(tid).status == TaskStatus.COMPLETED

    def test_partial_status_alerts(self):
        mgr = BackfillManager()
        done = threading.Event()

        def target(task_id):
            mgr._finish(task_id, TaskStatus.PARTIAL, "2 batches failed")

        with patch("trading_signals.utils.alerting.notify_run_status") as notify:
            notify.side_effect = lambda *a: done.set()
            tid = mgr._start("op", "p", "Op", target)
            assert done.wait(5)
        assert mgr.get_status(tid).status == TaskStatus.PARTIAL
        assert notify.call_args.args == ("op", "partial", "2 batches failed")

    def test_prune_keeps_newest_finished(self):
        mgr = BackfillManager()
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(MAX_FINISHED_TASKS + 5):
            mgr._tasks[f"t{i}"] = BackfillTask(
                task_id=f"t{i}",
                operation="op",
                status=TaskStatus.COMPLETED,
                completed_at=base + timedelta(minutes=i),
            )
        mgr._tasks["run"] = BackfillTask(
            task_id="run", operation="other", status=TaskStatus.RUNNING
        )
        mgr._prune_locked()
        assert len(mgr._tasks) == MAX_FINISHED_TASKS + 1
        assert "run" in mgr._tasks
        assert "t0" not in mgr._tasks and f"t{MAX_FINISHED_TASKS + 4}" in mgr._tasks

    def test_resolve_start_date_clamps(self):
        floor = date(2021, 7, 1)
        with patch(
            "trading_signals.utils.retention.data_start_date", return_value=floor
        ):
            assert resolve_start_date(None) == floor
            assert resolve_start_date("2019-01-01") == floor
            assert resolve_start_date("2024-03-01") == date(2024, 3, 1)
            with pytest.raises(ValueError):
                resolve_start_date("gestern")


# ── Dashboard cache ─────────────────────────────────────────────────────


def test_dashboard_stats_cache():
    dashboard.clear_stats_cache()
    compute = MagicMock(return_value=[1])
    assert dashboard._cached("k", compute) == [1]
    assert dashboard._cached("k", compute) == [1]
    assert compute.call_count == 1
    assert dashboard._cached("k", compute, ttl=0) == [1]
    assert compute.call_count == 2
    dashboard.clear_stats_cache()
    dashboard._cached("k", compute)
    assert compute.call_count == 3
    dashboard.clear_stats_cache()


# ── TZDateTime ──────────────────────────────────────────────────────────


class TestTZDateTime:
    t = TZDateTime()

    def test_naive_bind_is_berlin(self):
        out = self.t.process_bind_param(datetime(2026, 7, 1, 22, 30), None)
        assert out.tzinfo == BERLIN
        assert out.utcoffset() == timedelta(hours=2)

    def test_aware_bind_unchanged(self):
        aware = datetime(2026, 7, 1, 20, 30, tzinfo=UTC)
        assert self.t.process_bind_param(aware, None) is aware

    def test_date_bind_is_berlin_midnight(self):
        out = self.t.process_bind_param(date(2026, 1, 15), None)
        assert out == datetime(2026, 1, 15, 0, 0, tzinfo=BERLIN)

    def test_result_is_naive_berlin_wall_clock(self):
        out = self.t.process_result_value(
            datetime(2026, 1, 15, 21, 0, tzinfo=UTC), None
        )
        assert out == datetime(2026, 1, 15, 22, 0)
        assert out.tzinfo is None

    def test_none_passthrough(self):
        assert self.t.process_bind_param(None, None) is None
        assert self.t.process_result_value(None, None) is None


# ── main.py ─────────────────────────────────────────────────────────────


class TestMainApp:
    def test_health_degraded_without_scheduler_or_db(self):
        from trading_signals import main

        with (
            patch.object(main, "_scheduler", None),
            patch.object(main, "_db_ping", return_value=False),
        ):
            r = TestClient(main.app).get("/api/v1/health")
        assert r.status_code == 503
        assert r.json()["status"] == "degraded"

    def test_health_ok(self):
        from trading_signals import main

        with (
            patch.object(main, "_scheduler", MagicMock(running=True)),
            patch.object(main, "_db_ping", return_value=True),
        ):
            r = TestClient(main.app).get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_write_routes_guarded(self):
        from trading_signals import main

        main.app.dependency_overrides[get_api_key] = lambda: ""
        try:
            r = TestClient(main.app).post("/api/v1/ops/db/reset?confirm=RESET")
        finally:
            main.app.dependency_overrides.pop(get_api_key, None)
        assert r.status_code == 403  # rejected before touching the DB
