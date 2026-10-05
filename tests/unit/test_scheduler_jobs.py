"""Tests for scheduler.jobs (retention, nightly chain, upstream checks)."""

from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from trading_signals.scheduler import jobs
from trading_signals.scheduler.runner import JobOutcome
from trading_signals.utils import job_status


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


@contextmanager
def _fake_session(session):
    yield session


# ── Data retention ──────────────────────────────────────────────────────


class TestRetentionStatements:
    def test_all_tables_and_order(self):
        labels = [label for label, _ in jobs.retention_statements(date(2021, 7, 1))]
        assert labels[0] == "news_sentiment"  # FK child before news_articles
        assert labels.index("news_sentiment") < labels.index("news_articles")
        for expected in (
            "prices_daily",
            "ark_holdings",
            "ark_deltas",
            "estimates_snapshot",
            "short_interest",
            "short_volume",
            "options_iv_snapshot",
            "macro_series",
        ):
            assert expected in labels
        assert "earnings_calendar" not in labels
        assert "collection_log" not in labels

    def test_event_date_columns(self):
        stmts = dict(jobs.retention_statements(date(2021, 7, 1)))
        assert "filing_date <" in _sql(stmts["insider_trades"])
        assert "disclosure_date <" in _sql(stmts["politician_trades"])
        assert "filing_date <" in _sql(stmts["form13f_holdings"])
        sentiment = _sql(stmts["news_sentiment"])
        assert "article_id IN" in sentiment and "published_at <" in sentiment

    def test_bulk_delete_without_session_sync(self):
        for _, stmt in jobs.retention_statements(date(2021, 7, 1)):
            assert stmt.get_execution_options()["synchronize_session"] is False


class TestPurgeOldData:
    def test_failing_table_does_not_undo_others(self):
        session = MagicMock()
        calls = {"n": 0}

        def execute(stmt):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("lock timeout")
            return MagicMock(rowcount=3)

        session.execute.side_effect = execute
        with patch(
            "trading_signals.db.session.get_session",
            lambda: _fake_session(session),
        ):
            deleted, errors = jobs.purge_old_data(date(2021, 7, 1))

        total = len(jobs.retention_statements(date(2021, 7, 1)))
        assert len(errors) == 1
        assert len(deleted) == total - 1
        assert all(v == 3 for v in deleted.values())
        assert session.commit.call_count == total - 1
        session.rollback.assert_called_once()
        assert session.begin_nested.call_count == total

    def test_body_status(self):
        with patch.object(jobs, "purge_old_data", return_value=({"a": 2}, {})):
            assert jobs._data_retention_body().status == job_status.SUCCESS
        with patch.object(
            jobs, "purge_old_data", return_value=({"a": 2}, {"b": "err"})
        ):
            out = jobs._data_retention_body()
            assert out.status == job_status.PARTIAL
            assert out.records_written == 2
            assert "b: err" in out.notes
        with patch.object(jobs, "purge_old_data", return_value=({}, {"b": "err"})):
            assert jobs._data_retention_body().status == job_status.FAILED

    def test_quarter_cutoff_reexported(self):
        assert jobs.DATA_RETENTION_QUARTERS == 20
        assert callable(jobs.quarter_cutoff)


# ── Feature pipeline step ───────────────────────────────────────────────


class TestFeaturePipelineBody:
    def _run(self, written):
        pipeline = MagicMock()
        pipeline.return_value.compute_daily.return_value = written
        with (
            patch(
                "trading_signals.db.session.get_session",
                lambda: _fake_session(MagicMock()),
            ),
            patch("trading_signals.derived.feature_pipeline.FeaturePipeline", pipeline),
        ):
            out = jobs._feature_pipeline_body(date(2026, 10, 2))
        pipeline.return_value.compute_daily.assert_called_once_with(date(2026, 10, 2))
        return out

    def test_rows_written_is_success(self):
        out = self._run(480)
        assert out.status == job_status.SUCCESS
        assert out.records_written == 480
        assert out.notes == "session=2026-10-02"

    def test_zero_rows_is_skipped(self):
        out = self._run(0)
        assert out.status == job_status.SKIPPED
        assert out.notes.startswith("session=2026-10-02")


# ── Upstream checks ─────────────────────────────────────────────────────


class TestUpstream:
    def test_check_upstream_classifies(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = [
            ("prices_alpaca", "success"),
            ("news_alpaca", job_status.RUNNING),
            ("fred_collector", "completed"),  # legacy success
            ("options_iv_collector", "failed"),
        ]
        with patch(
            "trading_signals.db.session.get_session",
            lambda: _fake_session(session),
        ):
            stale, running = jobs.check_upstream(date(2026, 10, 2))
        assert "prices_alpaca" not in stale
        assert "fred_collector" not in stale
        assert "news_alpaca" in stale and "options_iv_collector" in stale
        assert running == ["news_alpaca"]

    def test_wait_polls_until_not_running(self):
        results = iter([(["a"], ["a"]), (["a"], ["a"]), (["b"], [])])
        sleeps = []
        with patch.object(jobs, "check_upstream", side_effect=lambda t: next(results)):
            stale = jobs.wait_for_upstream(
                date(2026, 10, 2), sleep=sleeps.append, clock=lambda: 0.0
            )
        assert stale == ["b"]
        assert sleeps == [jobs.CHAIN_POLL_SECONDS] * 2

    def test_wait_is_bounded(self):
        clock = iter([0.0, 10.0, 10_000.0])
        with patch.object(jobs, "check_upstream", return_value=(["a"], ["a"])):
            stale = jobs.wait_for_upstream(
                date(2026, 10, 2),
                max_wait=timedelta(seconds=100),
                sleep=lambda s: None,
                clock=lambda: next(clock),
            )
        assert stale == ["a"]


# ── Nightly chain ───────────────────────────────────────────────────────


class TestNightlyChain:
    TARGET = date(2026, 10, 2)

    def _run(self, statuses, stale=(), done=False):
        calls = []

        def fake_run(job_id, collector_name, fn, *, alert=True):
            calls.append((collector_name, alert))
            return JobOutcome(status=statuses.get(collector_name, job_status.SUCCESS))

        with (
            patch.object(jobs, "_target_session", return_value=self.TARGET),
            patch.object(jobs, "_chain_done_for", return_value=done),
            patch.object(jobs, "wait_for_upstream", return_value=list(stale)),
            patch.object(jobs, "run_logged_job", side_effect=fake_run),
        ):
            out = jobs._nightly_chain_body()
        return out, calls

    def test_order_and_success(self):
        out, calls = self._run({})
        assert [c[0] for c in calls] == [
            "technical_indicators",
            "feature_pipeline",
            "target_backfill",
            "context_pack_generator",
        ]
        assert all(alert is False for _, alert in calls)  # one alert for the chain
        assert out.status == job_status.SUCCESS
        assert out.notes.startswith("session=2026-10-02")

    def test_pipeline_failure_skips_context_pack(self):
        out, calls = self._run({"feature_pipeline": job_status.FAILED})
        assert "context_pack_generator" not in [c[0] for c in calls]
        assert out.status == job_status.FAILED

    def test_pipeline_skipped_skips_context_pack_partial(self):
        out, calls = self._run({"feature_pipeline": job_status.SKIPPED})
        assert "context_pack_generator" not in [c[0] for c in calls]
        assert out.status == job_status.PARTIAL

    def test_stale_inputs_make_partial(self):
        out, _ = self._run({}, stale=["short_interest_collector"])
        assert out.status == job_status.PARTIAL
        assert "short_interest_collector" in out.notes

    def test_already_processed_session_is_skipped(self):
        out, calls = self._run({}, done=True)
        assert out.status == job_status.SKIPPED
        assert calls == []
