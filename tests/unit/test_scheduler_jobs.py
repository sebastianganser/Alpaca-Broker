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

    def test_forecast_evaluation_records_are_never_purged(self):
        # research results of a pre-registered test: written once, no retention
        stmts = jobs.retention_statements(date(2021, 7, 1))
        labels = {label for label, _ in stmts}
        sql = " ".join(_sql(stmt) for _, stmt in stmts)
        for table in (
            "forecasts",
            "forecast_reviews",
            "forecast_criterion_samples",
            "forecast_file_seals",
            "pack_candidates",
        ):
            assert table not in labels, table
            assert f"signals.{table} " not in sql + " ", table
        assert "forecast" not in sql and "pack_candidates" not in sql
        doc = jobs.run_data_retention.__doc__
        assert "forecast_criterion_samples" in doc and "pack_candidates" in doc

    def test_event_date_columns(self):
        stmts = dict(jobs.retention_statements(date(2021, 7, 1)))
        assert "filing_date <" in _sql(stmts["insider_trades"])
        assert "disclosure_date <" in _sql(stmts["politician_trades"])
        assert "filing_date <" in _sql(stmts["form13f_holdings"])
        sentiment = _sql(stmts["news_sentiment"])
        assert "article_id IN" in sentiment and "published_at <" in sentiment

    def test_sec_tables_are_purged_by_the_date_they_became_public(self):
        stmts = dict(jobs.retention_statements(date(2021, 7, 1)))
        labels = list(stmts)
        # text excerpts first (child of the filing), then the filings
        assert labels.index("sec_filing_texts") < labels.index("sec_filings")
        texts = _sql(stmts["sec_filing_texts"])
        assert texts.startswith("DELETE FROM signals.sec_filing_texts")
        assert "IN (SELECT signals.sec_filings.accession_number" in texts
        assert "signals.sec_filings.filing_date <" in texts
        filings = _sql(stmts["sec_filings"])
        assert filings.startswith("DELETE FROM signals.sec_filings WHERE")
        assert "filing_date <" in filings
        financials = _sql(stmts["financials_quarterly"])
        assert financials.startswith("DELETE FROM signals.financials_quarterly WHERE")
        assert "period_end <" in financials

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


class TestCatchupWindow:
    def test_ten_sessions_before_the_target_oldest_first(self):
        window = jobs._catchup_window(date(2026, 10, 2))
        assert jobs.CATCHUP_LOOKBACK_SESSIONS == 10
        assert window == [
            date(2026, 9, 18),
            date(2026, 9, 21),
            date(2026, 9, 22),
            date(2026, 9, 23),
            date(2026, 9, 24),
            date(2026, 9, 25),
            date(2026, 9, 28),
            date(2026, 9, 29),
            date(2026, 9, 30),
            date(2026, 10, 1),
        ]

    def test_target_is_excluded_and_holidays_are_skipped(self):
        target = date(2026, 1, 5)  # Monday after New Year; Dec 25 / Jan 1 closed
        window = jobs._catchup_window(target)
        assert len(window) == 10
        assert window == sorted(window)
        assert window[-1] == date(2026, 1, 2)
        assert target not in window
        assert date(2026, 1, 1) not in window
        assert date(2025, 12, 25) not in window


class TestSnapshotCounts:
    SESSIONS = [date(2026, 9, 30), date(2026, 10, 1)]

    def _run(self, rows, sessions=None):
        session = MagicMock()
        session.execute.return_value.all.return_value = rows
        with patch(
            "trading_signals.db.session.get_session",
            lambda: _fake_session(session),
        ):
            if sessions is None:
                sessions = self.SESSIONS
            counts = jobs.snapshot_counts(sessions)
        return counts, session

    def test_counts_per_date_and_missing_dates_are_zero(self):
        counts, _ = self._run([(date(2026, 10, 1), 740)])
        assert counts == {date(2026, 9, 30): 0, date(2026, 10, 1): 740}

    def test_no_rows_at_all(self):
        counts, _ = self._run([])
        assert counts == {d: 0 for d in self.SESSIONS}

    def test_one_grouped_query_over_the_given_sessions(self):
        _, session = self._run([])
        session.execute.assert_called_once()
        stmt = session.execute.call_args.args[0]
        sql = _sql(stmt)
        assert "signals.feature_snapshots" in sql
        assert "GROUP BY signals.feature_snapshots.snapshot_date" in sql
        in_values = [
            v
            for v in stmt.compile(dialect=postgresql.dialect()).params.values()
            if isinstance(v, list)
        ]
        assert len(in_values) == 1
        assert sorted(in_values[0]) == self.SESSIONS

    def test_no_sessions_makes_no_query(self):
        counts, session = self._run([], sessions=[])
        assert counts == {}
        session.execute.assert_not_called()


class TestCatchupCandidates:
    D = [date(2026, 9, 28) + timedelta(days=i) for i in range(5)]  # Mon-Fri

    def _counts(self, *values):
        return dict(zip(self.D, values, strict=True))

    def test_nothing_computed_in_the_window_is_no_candidate(self):
        # fresh system: the rebuild scripts are responsible, not the chain
        assert jobs.catchup_candidates(self.D, {}) == []
        assert jobs.catchup_candidates(self.D, self._counts(0, 0, 0, 0, 0)) == []

    def test_empty_window(self):
        assert jobs.catchup_candidates([], {}) == []

    def test_complete_window_has_no_candidates(self):
        assert jobs.catchup_candidates(self.D, self._counts(*[767] * 5)) == []

    def test_missing_session_is_a_candidate(self):
        counts = self._counts(767, 767, 0, 767, 767)
        assert jobs.catchup_candidates(self.D, counts) == [self.D[2]]

    def test_session_absent_from_counts_counts_zero(self):
        counts = self._counts(767, 767, 767, 767, 767)
        del counts[self.D[1]]
        assert jobs.catchup_candidates(self.D, counts) == [self.D[1]]

    def test_partial_day_below_threshold_is_a_candidate(self):
        # 559/700 = 79.86 %
        counts = self._counts(700, 559, 700, 700, 700)
        assert jobs.catchup_candidates(self.D, counts) == [self.D[1]]

    def test_exactly_at_threshold_is_not_a_candidate(self):
        assert jobs.MIN_SNAPSHOT_COVERAGE == 0.8
        for values in (
            (700, 560, 700, 700, 700),
            (100, 80, 100, 100, 100),
            (5, 4, 5, 5, 5),
        ):
            assert jobs.catchup_candidates(self.D, self._counts(*values)) == []

    def test_baseline_is_the_window_maximum(self):
        # 600 is 78 % of the best day (767) although the neighbours are equal
        counts = self._counts(600, 600, 767, 600, 600)
        assert jobs.catchup_candidates(self.D, counts) == [
            self.D[0],
            self.D[1],
            self.D[3],
            self.D[4],
        ]

    def test_oldest_first(self):
        counts = self._counts(0, 767, 0, 767, 100)
        assert jobs.catchup_candidates(self.D, counts) == [
            self.D[0],
            self.D[2],
            self.D[4],
        ]
        shuffled = [self.D[4], self.D[0], self.D[3], self.D[2], self.D[1]]
        assert jobs.catchup_candidates(shuffled, counts) == [
            self.D[0],
            self.D[2],
            self.D[4],
        ]

    def test_custom_ratio(self):
        counts = self._counts(100, 50, 100, 100, 100)
        assert jobs.catchup_candidates(self.D, counts, min_ratio=0.5) == []
        assert jobs.catchup_candidates(self.D, counts, min_ratio=0.6) == [self.D[1]]


# ── Nightly chain ───────────────────────────────────────────────────────


class TestNightlyChain:
    TARGET = date(2026, 10, 2)
    ALL_STEPS = [
        "technical_indicators",
        "feature_pipeline",
        "target_backfill",
        "forecast_review",
        "context_pack_generator",
    ]
    # collector name -> body function the chain hands to run_logged_job
    BODIES = {
        "technical_indicators": "_technical_indicators_body",
        "feature_pipeline": "_feature_pipeline_body",
        "target_backfill": "_target_backfill_body",
        "forecast_review": "_forecast_review_body",
        "context_pack_generator": "_context_pack_body",
    }

    def _run(
        self,
        statuses,
        stale=(),
        done=False,
        coverage=(700, 700),
        coverage_rows=None,
        snapshots=None,
        gap_coverage=None,
    ):
        """Run the chain with ``run_logged_job`` executing the (mocked) bodies.

        ``coverage`` is the ``price_coverage`` result (or an exception it
        raises); ``coverage_rows`` instead feeds the real ``price_coverage``
        with fake grouped-query rows. ``statuses`` maps a collector name (or
        ``(name, date)`` for one feature-pipeline date) to its body status
        (or a full ``JobOutcome``).
        ``snapshots`` are the ``snapshot_counts`` of the catch-up window: a
        ``{date: count}`` override on top of a complete window (700 each) or an
        exception it raises; ``gap_coverage`` overrides ``price_coverage`` per
        date (tuple, or exception to raise). After the call ``self.job_ids`` /
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
            def run(*a):
                default = statuses.get(collector_name, job_status.SUCCESS)
                key = (collector_name, a[0] if a else None)
                result = statuses.get(key, default)
                if isinstance(result, JobOutcome):
                    return result
                return JobOutcome(status=result)

            return run

        def fake_snapshots(sessions):
            return {d: (snapshots or {}).get(d, 700) for d in sessions}

        def fake_coverage(d):
            result = (gap_coverage or {}).get(d, coverage)
            if isinstance(result, Exception):
                raise result
            return result

        with ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(jobs, "_target_session", return_value=self.TARGET))
            enter(patch.object(jobs, "_chain_done_for", return_value=done))
            self.wait_mock = enter(
                patch.object(jobs, "wait_for_upstream", return_value=list(stale))
            )
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
            else:
                self.coverage_mock = enter(
                    patch.object(jobs, "price_coverage", side_effect=fake_coverage)
                )
            self.snapshot_mock = enter(
                patch.object(
                    jobs,
                    "snapshot_counts",
                    side_effect=(
                        snapshots
                        if isinstance(snapshots, Exception)
                        else fake_snapshots
                    ),
                )
            )
            out = jobs._nightly_chain_body()
        return out, calls

    def test_order_and_success(self):
        out, calls = self._run({})
        assert [c[0] for c in calls] == self.ALL_STEPS
        assert all(alert is False for _, alert in calls)  # one alert for the chain
        assert out.status == job_status.SUCCESS
        assert out.notes.startswith("session=2026-10-02")

    def test_invalid_forecast_file_makes_partial(self):
        out, calls = self._run({"forecast_review": job_status.PARTIAL})
        assert "context_pack_generator" in [c[0] for c in calls]
        assert out.status == job_status.PARTIAL
        assert "forecast_review=partial" in out.notes

    def test_a_kept_pack_does_not_make_the_chain_partial_and_is_noted(self):
        # a PARTIAL chain re-run for the same session (weekend, holiday) must
        # not rewrite a pack Claude has already worked on: the generator keeps
        # it, the step still succeeds and the chain says so
        kept = JobOutcome(
            notes="session=2026-10-02; pack kept (forecasts.yaml exists) - "
            "not regenerated"
        )
        out, calls = self._run({"context_pack_generator": kept})
        assert out.status == job_status.SUCCESS
        assert "context_pack=success" in out.notes
        assert "context pack: pack kept (forecasts.yaml exists)" in out.notes
        # without the note the chain notes carry nothing about the pack
        plain, _ = self._run({})
        assert "pack kept" not in plain.notes

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
        for name in ("technical_indicators", "target_backfill", "forecast_review"):
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
        self.bodies["forecast_review"].assert_called_once()
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
        for name in ("technical_indicators", "target_backfill", "forecast_review"):
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
        assert out.notes == "session=2026-10-02; no new session"
        assert calls == []

    # ── automatic catch-up of missed sessions ───────────────────────────

    GAP = date(2026, 9, 30)
    OLD_GAP = date(2026, 9, 28)
    WINDOW = [
        date(2026, 9, 18),
        date(2026, 9, 21),
        date(2026, 9, 22),
        date(2026, 9, 23),
        date(2026, 9, 24),
        date(2026, 9, 25),
        date(2026, 9, 28),
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 1),
    ]

    def _fp_dates(self):
        """Dates the feature-pipeline body was called with, in order."""
        return [c.args[0] for c in self.bodies["feature_pipeline"].call_args_list]

    def test_complete_window_has_no_catchup(self):
        out, calls = self._run({})
        self.snapshot_mock.assert_called_once_with(self.WINDOW)
        assert [c[0] for c in calls] == self.ALL_STEPS
        assert "catch-up" not in out.notes
        # price coverage is only looked up for the target, not for the window
        self.coverage_mock.assert_called_once_with(self.TARGET)

    def test_gap_with_complete_prices_is_caught_up_before_target_backfill(self):
        out, calls = self._run({}, snapshots={self.GAP: 0})
        assert [c[0] for c in calls] == [
            "technical_indicators",
            "feature_pipeline",  # gap session
            "feature_pipeline",  # target session
            "target_backfill",
            "forecast_review",
            "context_pack_generator",
        ]
        assert all(alert is False for _, alert in calls)
        assert self._fp_dates() == [self.GAP, self.TARGET]
        self.coverage_mock.assert_any_call(self.GAP)
        # no context pack for the past session
        self.bodies["context_pack_generator"].assert_called_once_with(self.TARGET)
        assert out.status == job_status.SUCCESS
        # the existing note prefix is untouched, the catch-up part is appended
        assert out.notes == (
            "session=2026-10-02; technical_indicators=success, "
            "feature_pipeline=success, target_backfill=success, "
            "forecast_review=success, context_pack=success; "
            "catch-up: 2026-09-30=success"
        )

    def test_partial_snapshot_day_is_caught_up(self):
        out, _ = self._run({}, snapshots={self.GAP: 500})  # 500/700 = 71 %
        assert self._fp_dates() == [self.GAP, self.TARGET]
        assert out.notes.endswith("; catch-up: 2026-09-30=success")

    def test_several_gaps_are_processed_oldest_first(self):
        out, _ = self._run({}, snapshots={self.GAP: 0, self.OLD_GAP: 0})
        assert self._fp_dates() == [self.OLD_GAP, self.GAP, self.TARGET]
        assert out.notes.endswith("; catch-up: 2026-09-28=success, 2026-09-30=success")
        assert out.status == job_status.SUCCESS

    def test_gap_with_incomplete_prices_is_pending(self):
        out, calls = self._run(
            {}, snapshots={self.GAP: 0}, gap_coverage={self.GAP: (300, 700)}
        )
        assert self._fp_dates() == [self.TARGET]  # no feature run for the gap
        assert [c[0] for c in calls] == self.ALL_STEPS
        assert out.notes.endswith("; catch-up: 2026-09-30=pending (prices 42%)")
        # the original night already alerted - pending alone does not change it
        assert out.status == job_status.SUCCESS

    def test_gap_without_any_prices_is_pending_with_zero_percent(self):
        out, _ = self._run({}, snapshots={self.GAP: 0}, gap_coverage={self.GAP: (0, 0)})
        assert self._fp_dates() == [self.TARGET]
        assert "catch-up: 2026-09-30=pending (prices 0%)" in out.notes
        assert out.status == job_status.SUCCESS

    def test_pending_and_complete_gaps_are_judged_individually(self):
        out, _ = self._run(
            {},
            snapshots={self.GAP: 0, self.OLD_GAP: 0},
            gap_coverage={self.OLD_GAP: (350, 700)},
        )
        assert self._fp_dates() == [self.GAP, self.TARGET]
        assert out.notes.endswith(
            "; catch-up: 2026-09-28=pending (prices 50%), 2026-09-30=success"
        )

    def test_failed_catchup_makes_partial_and_other_steps_still_run(self):
        out, calls = self._run(
            {("feature_pipeline", self.GAP): job_status.FAILED},
            snapshots={self.GAP: 0},
        )
        assert self._fp_dates() == [self.GAP, self.TARGET]
        assert [c[0] for c in calls][-3:] == [
            "target_backfill",
            "forecast_review",
            "context_pack_generator",
        ]
        assert out.status == job_status.PARTIAL
        assert out.notes.endswith("; catch-up: 2026-09-30=failed")
        assert "feature_pipeline=success" in out.notes  # the target itself is fine

    def test_catchup_session_exception_is_recorded_and_next_session_runs(self):
        out, calls = self._run(
            {},
            snapshots={self.GAP: 0, self.OLD_GAP: 0},
            gap_coverage={self.OLD_GAP: RuntimeError("db down")},
        )
        assert self._fp_dates() == [self.GAP, self.TARGET]
        assert [c[0] for c in calls][-3:] == [
            "target_backfill",
            "forecast_review",
            "context_pack_generator",
        ]
        assert out.status == job_status.PARTIAL
        assert out.notes.endswith("; catch-up: 2026-09-28=failed, 2026-09-30=success")
        assert "db down" not in out.notes

    def test_snapshot_counts_exception_does_not_abort_the_chain(self):
        out, calls = self._run({}, snapshots=RuntimeError("db down"))
        assert [c[0] for c in calls] == self.ALL_STEPS
        assert out.status == job_status.PARTIAL
        assert out.notes.startswith("session=2026-10-02; technical_indicators=success")
        assert out.notes.endswith("; catch-up: detection failed (RuntimeError)")
        assert "db down" not in out.notes  # only the type, not the message

    def test_failed_catchup_does_not_soften_a_failed_chain(self):
        out, _ = self._run(
            {"feature_pipeline": job_status.FAILED}, snapshots=RuntimeError("x")
        )
        assert out.status == job_status.FAILED

    def test_critical_target_still_catches_up_earlier_gaps(self):
        out, calls = self._run(
            {},
            stale=["prices_alpaca", "fred_collector"],
            snapshots={self.GAP: 0},
        )
        # the target's feature pipeline and context pack are skipped ...
        assert self._fp_dates() == [self.GAP]
        self.bodies["context_pack_generator"].assert_not_called()
        assert out.status == job_status.FAILED
        # ... but the earlier session with complete prices is filled
        assert [c[0] for c in calls] == [
            "technical_indicators",
            "feature_pipeline",  # gap session (runs)
            "feature_pipeline",  # target session (skipped row)
            "target_backfill",
            "forecast_review",
            "context_pack_generator",
        ]
        # the target's SKIPPED row stays the last feature_pipeline log entry
        assert self.logged["feature_pipeline"].status == job_status.SKIPPED
        assert out.notes.startswith("session=2026-10-02; ")
        assert f"feature_pipeline={job_status.SKIPPED}" in out.notes
        # order of the appended parts: critical, catch-up, stale inputs
        i_crit = out.notes.index("; critical inputs stale: ")
        i_catchup = out.notes.index("; catch-up: 2026-09-30=success")
        i_stale = out.notes.index("; stale inputs: fred_collector")
        assert i_crit < i_catchup < i_stale
        assert out.notes.endswith("; stale inputs: fred_collector")

    def test_low_target_coverage_does_not_block_gap_with_complete_prices(self):
        out, _ = self._run(
            {},
            coverage=(512, 741),
            snapshots={self.GAP: 0, self.OLD_GAP: 0},
            gap_coverage={self.GAP: (741, 741), self.OLD_GAP: (400, 741)},
        )
        assert self._fp_dates() == [self.GAP]
        assert out.status == job_status.FAILED
        assert (
            "critical inputs stale: prices_alpaca "
            "(coverage 512/741 = 69% of 5-session max)"
        ) in out.notes
        assert out.notes.endswith(
            "; catch-up: 2026-09-28=pending (prices 53%), 2026-09-30=success"
        )

    def test_catchup_note_comes_before_failed_step_notes(self):
        out, _ = self._run(
            {"target_backfill": JobOutcome(status=job_status.FAILED, notes="boom")},
            snapshots={self.GAP: 0},
        )
        assert out.status == job_status.PARTIAL
        assert out.notes.endswith(
            "; catch-up: 2026-09-30=success; target_backfill: boom"
        )

    # target session already processed (weekend, restart, manual trigger)

    def test_done_target_with_gap_runs_catchup_only(self):
        out, calls = self._run({}, done=True, snapshots={self.GAP: 0})
        assert [c[0] for c in calls] == [
            "technical_indicators",
            "feature_pipeline",
            "target_backfill",
        ]
        assert all(alert is False for _, alert in calls)
        # nothing for the already processed target; upstream is irrelevant
        assert self._fp_dates() == [self.GAP]
        self.bodies["forecast_review"].assert_not_called()
        self.bodies["context_pack_generator"].assert_not_called()
        self.wait_mock.assert_not_called()
        assert out.status == job_status.SUCCESS
        assert out.notes == (
            "session=2026-10-02; catch-up only; "
            "technical_indicators=success, target_backfill=success; "
            "catch-up: 2026-09-30=success"
        )

    def test_done_target_with_pending_gap_is_still_success(self):
        out, calls = self._run(
            {},
            done=True,
            snapshots={self.GAP: 0},
            gap_coverage={self.GAP: (300, 700)},
        )
        assert self._fp_dates() == []
        assert [c[0] for c in calls] == ["technical_indicators", "target_backfill"]
        assert out.status == job_status.SUCCESS
        assert out.notes.endswith("; catch-up: 2026-09-30=pending (prices 42%)")

    def test_done_target_with_failed_catchup_is_partial(self):
        out, calls = self._run(
            {("feature_pipeline", self.GAP): job_status.FAILED},
            done=True,
            snapshots={self.GAP: 0},
        )
        assert [c[0] for c in calls][-1] == "target_backfill"  # still runs
        assert out.status == job_status.PARTIAL
        assert out.notes.startswith("session=2026-10-02; catch-up only; ")
        assert out.notes.endswith("; catch-up: 2026-09-30=failed")

    def test_done_target_with_failing_step_is_partial(self):
        out, _ = self._run(
            {"target_backfill": job_status.FAILED}, done=True, snapshots={self.GAP: 0}
        )
        assert out.status == job_status.PARTIAL
        assert "target_backfill=failed" in out.notes

    def test_done_target_with_failing_detection_is_visible_but_runs_nothing(self):
        out, calls = self._run({}, done=True, snapshots=RuntimeError("db down"))
        assert calls == []
        assert out.status == job_status.PARTIAL
        assert out.notes == (
            "session=2026-10-02; no new session; "
            "catch-up: detection failed (RuntimeError)"
        )
