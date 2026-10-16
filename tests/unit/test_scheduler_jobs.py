"""Tests for scheduler.jobs (retention, nightly chain, upstream checks)."""

from contextlib import ExitStack, contextmanager
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


# ── Price coverage ──────────────────────────────────────────────────────


class TestCoverageOk:
    def test_no_baseline_is_ok(self):
        assert jobs.coverage_ok(5, 0) is True  # bootstrap: nothing to compare

    def test_no_target_rows_is_never_ok(self):
        assert jobs.coverage_ok(0, 0) is False
        assert jobs.coverage_ok(0, 700) is False

    def test_exactly_at_threshold_is_ok(self):
        assert jobs.MIN_PRICE_COVERAGE == 0.8
        assert jobs.coverage_ok(80, 100) is True
        assert jobs.coverage_ok(4, 5) is True

    def test_below_threshold_fails(self):
        assert jobs.coverage_ok(79, 100) is False
        assert jobs.coverage_ok(512, 741) is False
        assert jobs.coverage_ok(0, 741) is False

    def test_full_or_higher_coverage_is_ok(self):
        assert jobs.coverage_ok(741, 741) is True
        assert jobs.coverage_ok(760, 741) is True

    def test_custom_ratio(self):
        assert jobs.coverage_ok(50, 100, min_ratio=0.5) is True
        assert jobs.coverage_ok(49, 100, min_ratio=0.5) is False


class TestPriceCoverage:
    TARGET = date(2026, 10, 2)  # Friday
    # the 5 NYSE sessions before it (oldest first); 2026-09-24 is the 6th
    WINDOW = [
        date(2026, 9, 25),
        date(2026, 9, 28),
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 1),
    ]

    def _run(self, rows):
        session = MagicMock()
        session.execute.return_value.all.return_value = rows
        with patch(
            "trading_signals.db.session.get_session",
            lambda: _fake_session(session),
        ):
            counts = jobs.price_coverage(self.TARGET)
        return counts, session.execute.call_args.args[0]

    def test_baseline_is_max_over_last_sessions(self):
        counts, _ = self._run(
            [
                (self.TARGET, 700),
                (date(2026, 10, 1), 740),
                (date(2026, 9, 30), 767),
                (date(2026, 9, 29), 750),
            ]
        )
        assert counts == (700, 767)

    def test_missing_target_session_counts_zero(self):
        counts, _ = self._run([(date(2026, 10, 1), 740)])
        assert counts == (0, 740)

    def test_no_rows_at_all(self):
        counts, _ = self._run([])
        assert counts == (0, 0)

    def test_multi_night_outage_keeps_pre_outage_baseline(self):
        # the same 467 tickers failed for four nights; comparing with the
        # previous night only would read 467/467 = 100 %
        counts, _ = self._run(
            [
                (self.WINDOW[0], 767),
                (self.WINDOW[1], 467),
                (self.WINDOW[2], 467),
                (self.WINDOW[3], 467),
                (self.WINDOW[4], 467),
                (self.TARGET, 467),
            ]
        )
        assert counts == (467, 767)
        assert jobs.coverage_ok(*counts) is False

    def test_sessions_before_the_window_do_not_count(self):
        # outage older than COVERAGE_BASELINE_SESSIONS nights is the new normal
        assert jobs.COVERAGE_BASELINE_SESSIONS == 5
        counts, _ = self._run(
            [(date(2026, 9, 24), 767), (date(2026, 10, 1), 467), (self.TARGET, 467)]
        )
        assert counts == (467, 467)

    def test_queries_target_and_last_five_sessions_only(self):
        _, stmt = self._run([])
        in_values = [
            v
            for v in stmt.compile(dialect=postgresql.dialect()).params.values()
            if isinstance(v, list)
        ]
        assert len(in_values) == 1
        assert sorted(in_values[0]) == [*self.WINDOW, self.TARGET]

    def test_filters_extrapolated_and_groups_by_date(self):
        _, stmt = self._run([])
        sql = _sql(stmt)
        assert "is_extrapolated IS NOT true" in sql  # NULL counts as real
        assert "GROUP BY signals.prices_daily.trade_date" in sql


# ── Nightly chain ───────────────────────────────────────────────────────


class TestNightlyChain:
    TARGET = date(2026, 10, 2)
    ALL_STEPS = [
        "technical_indicators",
        "feature_pipeline",
        "target_backfill",
        "stage2_review",
        "context_pack_generator",
    ]
    # collector name -> body function the chain hands to run_logged_job
    BODIES = {
        "technical_indicators": "_technical_indicators_body",
        "feature_pipeline": "_feature_pipeline_body",
        "target_backfill": "_target_backfill_body",
        "stage2_review": "_stage2_review_body",
        "context_pack_generator": "_context_pack_body",
    }

    def _run(
        self,
        statuses,
        stale=(),
        done=False,
        coverage=(700, 700),
        coverage_rows=None,
    ):
        """Run the chain with ``run_logged_job`` executing the (mocked) bodies.

        ``coverage`` is the ``price_coverage`` result (or an exception it
        raises); ``coverage_rows`` instead feeds the real ``price_coverage``
        with fake grouped-query rows. After the call ``self.job_ids`` /
        ``self.logged`` hold job id and outcome per collector name and
        ``self.bodies`` the body mocks (to check what really ran).
        """
        calls = []
        self.job_ids = {}
        self.logged = {}

        def fake_run(job_id, collector_name, fn, *, alert=True):
            calls.append((collector_name, alert))
            self.job_ids[collector_name] = job_id
            self.logged[collector_name] = outcome = fn()
            return outcome

        def body(collector_name):
            return lambda *a: JobOutcome(
                status=statuses.get(collector_name, job_status.SUCCESS)
            )

        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(jobs, "_target_session", return_value=self.TARGET))
            enter(patch.object(jobs, "_chain_done_for", return_value=done))
            enter(patch.object(jobs, "wait_for_upstream", return_value=list(stale)))
            enter(patch.object(jobs, "run_logged_job", side_effect=fake_run))
            self.bodies = {
                name: enter(patch.object(jobs, attr, side_effect=body(name)))
                for name, attr in self.BODIES.items()
            }
            if coverage_rows is not None:
                self.coverage_mock = None
                session = MagicMock()
                session.execute.return_value.all.return_value = coverage_rows
                enter(
                    patch(
                        "trading_signals.db.session.get_session",
                        lambda: _fake_session(session),
                    )
                )
            elif isinstance(coverage, Exception):
                self.coverage_mock = enter(
                    patch.object(jobs, "price_coverage", side_effect=coverage)
                )
            else:
                self.coverage_mock = enter(
                    patch.object(jobs, "price_coverage", return_value=coverage)
                )
            out = jobs._nightly_chain_body()
        return out, calls

    def test_order_and_success(self):
        out, calls = self._run({})
        assert [c[0] for c in calls] == self.ALL_STEPS
        assert all(alert is False for _, alert in calls)  # one alert for the chain
        assert out.status == job_status.SUCCESS
        assert out.notes.startswith("session=2026-10-02")

    def test_invalid_decisions_file_makes_partial(self):
        out, calls = self._run({"stage2_review": job_status.PARTIAL})
        assert "context_pack_generator" in [c[0] for c in calls]
        assert out.status == job_status.PARTIAL
        assert "stage2_review=partial" in out.notes

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

    def test_non_critical_stale_still_runs_everything(self):
        out, calls = self._run(
            {}, stale=["short_interest_collector", "fred_collector"]
        )
        names = [c[0] for c in calls]
        assert "feature_pipeline" in names and "context_pack_generator" in names
        assert out.status == job_status.PARTIAL
        assert "stale inputs: short_interest_collector, fred_collector" in out.notes
        assert "critical" not in out.notes
        self.coverage_mock.assert_called_once_with(self.TARGET)

    def test_critical_stale_skips_features_and_context_pack(self):
        out, calls = self._run({}, stale=["prices_alpaca", "fred_collector"])
        # all five steps are logged, but the two skipped ones never run their body
        assert [c[0] for c in calls] == self.ALL_STEPS
        for name in ("feature_pipeline", "context_pack_generator"):
            self.bodies[name].assert_not_called()
        for name in ("technical_indicators", "target_backfill", "stage2_review"):
            self.bodies[name].assert_called_once()
        assert all(alert is False for _, alert in calls)
        assert out.status == job_status.FAILED
        assert out.records_written == 0
        assert out.notes.startswith("session=2026-10-02; ")
        assert f"feature_pipeline={job_status.SKIPPED}" in out.notes
        assert f"context_pack={job_status.SKIPPED}" in out.notes
        assert "; critical inputs stale: prices_alpaca; " in out.notes
        assert "features/context pack skipped" in out.notes
        assert (
            "scripts/recompute_features.py --start 2026-10-02 --end 2026-10-02 "
            "--all-trading-days"
        ) in out.notes
        # non-critical stale input keeps its own part, critical one is not repeated
        assert out.notes.endswith("; stale inputs: fred_collector")
        # run status already says the prices are missing - no coverage query needed
        self.coverage_mock.assert_not_called()

    def test_critical_records_skipped_log_rows_for_features_and_context_pack(self):
        # without these rows the dashboard keeps showing the previous night's
        # success as the last run of both steps
        out, calls = self._run({}, stale=["prices_alpaca"])
        assert ("feature_pipeline", False) in calls
        assert ("context_pack_generator", False) in calls
        for name in ("feature_pipeline", "context_pack_generator"):
            assert self.job_ids[name] == name
            logged = self.logged[name]
            assert logged.status == job_status.SKIPPED
            assert logged.notes == "critical input stale: prices_alpaca"
            self.bodies[name].assert_not_called()
        # the chain reports what the skipped rows carry
        assert f"feature_pipeline={job_status.SKIPPED}" in out.notes
        assert f"context_pack={job_status.SKIPPED}" in out.notes

    def test_no_skipped_rows_when_nothing_is_critical(self):
        self._run({})
        for name in ("feature_pipeline", "context_pack_generator"):
            assert self.logged[name].status == job_status.SUCCESS
            self.bodies[name].assert_called_once()

    def test_low_price_coverage_is_critical_although_prices_run_is_fresh(self):
        out, calls = self._run({}, stale=[], coverage=(512, 741))
        assert [c[0] for c in calls] == self.ALL_STEPS
        self.bodies["feature_pipeline"].assert_not_called()
        self.bodies["context_pack_generator"].assert_not_called()
        self.bodies["target_backfill"].assert_called_once()
        self.bodies["stage2_review"].assert_called_once()
        assert out.status == job_status.FAILED
        assert (
            "critical inputs stale: prices_alpaca "
            "(coverage 512/741 = 69% of 5-session max)"
        ) in out.notes
        assert "stale inputs" not in out.notes

    def test_multi_night_outage_is_still_critical(self):
        # 467 tickers failed for the last four nights and tonight again: the
        # baseline is the best of the last 5 sessions (767), not last night's
        rows = [
            (date(2026, 9, 25), 767),
            (date(2026, 9, 28), 467),
            (date(2026, 9, 29), 467),
            (date(2026, 9, 30), 467),
            (date(2026, 10, 1), 467),
            (self.TARGET, 467),
        ]
        out, _ = self._run({}, coverage_rows=rows)
        self.bodies["feature_pipeline"].assert_not_called()
        self.bodies["context_pack_generator"].assert_not_called()
        assert out.status == job_status.FAILED
        assert (
            "prices_alpaca (coverage 467/767 = 60% of 5-session max)" in out.notes
        )

    def test_price_coverage_exception_is_critical_and_other_steps_run(self):
        out, calls = self._run({}, coverage=RuntimeError("db down"))
        assert [c[0] for c in calls] == self.ALL_STEPS
        self.bodies["feature_pipeline"].assert_not_called()
        self.bodies["context_pack_generator"].assert_not_called()
        for name in ("technical_indicators", "target_backfill", "stage2_review"):
            self.bodies[name].assert_called_once()
        assert self.logged["feature_pipeline"].status == job_status.SKIPPED
        assert self.logged["context_pack_generator"].status == job_status.SKIPPED
        assert out.status == job_status.FAILED
        assert out.notes.startswith("session=2026-10-02; ")
        assert (
            "critical inputs stale: prices_alpaca (coverage check failed: RuntimeError)"
        ) in out.notes
        assert "db down" not in out.notes  # only the type, not the message

    def test_price_coverage_exception_is_skipped_when_prices_already_stale(self):
        # the run status already makes it critical - the query is never made
        out, _ = self._run({}, stale=["prices_alpaca"], coverage=RuntimeError("x"))
        self.coverage_mock.assert_not_called()
        assert "coverage check failed" not in out.notes

    def test_coverage_percentage_is_rounded_down(self):
        out, _ = self._run({}, coverage=(797, 1000))
        assert "coverage 797/1000 = 79%" in out.notes

    def test_coverage_at_threshold_builds_features(self):
        out, calls = self._run({}, coverage=(80, 100))
        assert "feature_pipeline" in [c[0] for c in calls]
        self.bodies["feature_pipeline"].assert_called_once()
        assert out.status == job_status.SUCCESS

    def test_no_baseline_prices_is_not_critical(self):
        out, calls = self._run({}, coverage=(650, 0))
        assert "feature_pipeline" in [c[0] for c in calls]
        self.bodies["feature_pipeline"].assert_called_once()
        assert out.status == job_status.SUCCESS

    def test_no_target_prices_at_all_is_critical(self):
        out, _ = self._run({}, coverage=(0, 0))
        self.bodies["feature_pipeline"].assert_not_called()
        assert out.status == job_status.FAILED
        assert "coverage 0/0 = 0%" in out.notes

    def test_already_processed_session_is_skipped(self):
        out, calls = self._run({}, done=True)
        assert out.status == job_status.SKIPPED
        assert calls == []
