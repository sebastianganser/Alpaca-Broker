"""Tests for scheduler setup and the job/collector registry."""

from trading_signals.scheduler.registry import (
    CATCHUP_JOBS,
    CHAIN_STEP_JOB_IDS,
    JOB_COLLECTOR_NAMES,
    MANUAL_SUFFIX,
    NIGHTLY_UPSTREAM,
    get_collector_name,
    normalize_job_id,
)
from trading_signals.scheduler.setup import JOB_DEFINITIONS, create_scheduler


def _hm(job):
    fields = {f.name: str(f) for f in job.trigger.fields}
    return fields["hour"], fields["minute"]


class TestCreateScheduler:
    def test_job_ids_unique_and_complete(self):
        ids = [d[0] for d in JOB_DEFINITIONS]
        assert len(ids) == len(set(ids))
        for jid in (
            "price_collector",
            "nightly_chain",
            "data_retention",
            "log_retention",
            "short_interest_collector",
            "short_interest_positions_collector",
            "universe_validation",
            "options_iv_collector",
            *CHAIN_STEP_JOB_IDS,
            *CATCHUP_JOBS,
        ):
            assert jid in ids

    def test_schedule_times(self):
        sched = create_scheduler()
        expected = {
            "price_collector": ("22", "30"),
            "technical_indicators_computer": ("22", "50"),
            "options_iv_collector": ("23", "15"),
            "short_interest_collector": ("0", "15"),
            "fred_collector": ("4", "15"),
            "nightly_chain": ("4", "30"),
        }
        for jid, hm in expected.items():
            assert _hm(sched.get_job(jid)) == hm, jid
        fa = {f.name: str(f) for f in sched.get_job("feature_analysis").trigger.fields}
        assert (fa["day"], fa["hour"], fa["minute"]) == ("1", "7", "0")

    def test_job_count(self):
        assert len(JOB_DEFINITIONS) == 27
        assert len(create_scheduler().get_jobs()) == 27

    def test_forecast_seal_fires_before_the_us_open_in_new_york_time(self):
        from datetime import UTC, datetime
        from zoneinfo import ZoneInfo

        job = create_scheduler().get_job("forecast_seal")
        f = {x.name: str(x) for x in job.trigger.fields}
        assert (f["day_of_week"], f["hour"], f["minute"]) == ("mon-fri", "9", "0,15,25")
        assert str(job.trigger.timezone) == "America/New_York"
        # not a chain step: it has its own schedule
        assert "forecast_seal" not in CHAIN_STEP_JOB_IDS
        # DST-proof: the three runs of a summer day and of a winter day are
        # 09:00 / 09:15 / 09:25 New York = 13:00-13:25 UTC resp. 14:00-14:25 UTC
        ny = ZoneInfo("America/New_York")
        summer, winter = datetime(2026, 10, 19), datetime(2026, 11, 30)
        for day, utc_hour in ((summer, 13), (winter, 14)):
            before = datetime(day.year, day.month, day.day, 0, 0, tzinfo=ny)
            fire = job.trigger.get_next_fire_time(None, before)
            times = []
            while fire is not None and fire.date() == before.date():
                times.append(fire.astimezone(UTC))
                fire = job.trigger.get_next_fire_time(fire, fire)
            assert [(t.hour, t.minute) for t in times] == [
                (utc_hour, 0), (utc_hour, 15), (utc_hour, 25)
            ]
        # a delayed run is dropped (and reported as missed) instead of running late
        assert job.misfire_grace_time == 300
        assert JOB_COLLECTOR_NAMES["forecast_seal"] == "forecast_seal"

    def test_forecast_seal_skips_the_weekend(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        job = create_scheduler().get_job("forecast_seal")
        ny = ZoneInfo("America/New_York")
        saturday = datetime(2026, 10, 17, 0, 0, tzinfo=ny)
        fire = job.trigger.get_next_fire_time(None, saturday)
        assert (fire.weekday(), fire.hour, fire.minute) == (0, 9, 0)  # Monday 09:00

    def test_forecast_review_replaces_stage2_review_in_the_chain(self):
        from trading_signals.scheduler import jobs

        # same position as the old step: after the target backfill, before the pack
        assert CHAIN_STEP_JOB_IDS == (
            "feature_pipeline", "target_backfill", "forecast_review",
            "context_pack_generator",
        )
        ids = [d[0] for d in JOB_DEFINITIONS]
        assert "forecast_review" in ids and "stage2_review" not in ids
        assert "stage2_review" not in JOB_COLLECTOR_NAMES
        definition = {d[0]: d for d in JOB_DEFINITIONS}["forecast_review"]
        assert definition[1] is jobs.run_forecast_review
        assert not hasattr(jobs, "run_stage2_review")
        assert JOB_COLLECTOR_NAMES["forecast_review"] == "forecast_review"
        sched = create_scheduler()
        assert _hm(sched.get_job("forecast_review")) == ("4", "30")  # paused chain step

    def test_universe_validation_runs_weekly_sunday_13_berlin(self):
        job = create_scheduler().get_job("universe_validation")
        f = {x.name: str(x) for x in job.trigger.fields}
        assert (f["day_of_week"], f["hour"], f["minute"]) == ("sun", "13", "0")
        assert str(job.trigger.timezone) == "Europe/Berlin"
        # after the other Sunday collectors (short interest positions at 12:00)
        pos = create_scheduler().get_job("short_interest_positions_collector")
        assert _hm(pos) == ("12", "0")
        # a regular scheduled job, not a paused chain step
        assert "universe_validation" not in CHAIN_STEP_JOB_IDS

    def test_short_interest_positions_runs_weekly_sunday_noon(self):
        job = create_scheduler().get_job("short_interest_positions_collector")
        f = {x.name: str(x) for x in job.trigger.fields}
        assert (f["day_of_week"], f["hour"], f["minute"]) == ("sun", "12", "0")
        # after the politician trades collector (Sunday 11:00)
        pol = create_scheduler().get_job("politician_trades_collector")
        assert _hm(pol) == ("11", "0")

    def test_chain_steps_paused_after_start(self):
        sched = create_scheduler()
        sched.start(paused=True)
        try:
            for jid in CHAIN_STEP_JOB_IDS:
                assert sched.get_job(jid).next_run_time is None, jid
            assert sched.get_job("nightly_chain").next_run_time is not None
            assert sched.get_job("price_collector").next_run_time is not None
        finally:
            sched.shutdown(wait=False)


class TestRegistry:
    def test_collector_name_mapping(self):
        assert get_collector_name("price_collector") == "prices_alpaca"
        assert get_collector_name("news_collector") == "news_alpaca"
        assert get_collector_name("fundamentals_collector") == "fundamentals_yf"
        assert (
            get_collector_name("technical_indicators_computer")
            == "technical_indicators"
        )
        # unknown ids map to themselves; manual clones are normalized
        assert get_collector_name("fred_collector") == "fred_collector"
        assert get_collector_name(f"price_collector{MANUAL_SUFFIX}") == "prices_alpaca"

    def test_normalize_job_id(self):
        assert normalize_job_id(f"feature_pipeline{MANUAL_SUFFIX}") == (
            "feature_pipeline"
        )
        assert normalize_job_id("nightly_chain") == "nightly_chain"

    def test_upstream_contains_prices(self):
        assert "prices_alpaca" in NIGHTLY_UPSTREAM

    def test_short_interest_positions_registry(self):
        job_id = "short_interest_positions_collector"
        assert JOB_COLLECTOR_NAMES[job_id] == "short_interest_positions"
        assert get_collector_name(job_id) == "short_interest_positions"
        assert get_collector_name(f"{job_id}{MANUAL_SUFFIX}") == (
            "short_interest_positions"
        )
        # features use a lagged availability rule -> not a nightly upstream
        assert "short_interest_positions" not in NIGHTLY_UPSTREAM
        assert job_id not in NIGHTLY_UPSTREAM

    def test_universe_validation_registry(self):
        job_id = "universe_validation"
        assert JOB_COLLECTOR_NAMES[job_id] == "universe_validation"
        assert get_collector_name(job_id) == "universe_validation"
        assert get_collector_name(f"{job_id}{MANUAL_SUFFIX}") == "universe_validation"
        # housekeeping: the nightly chain does not wait for it
        assert "universe_validation" not in NIGHTLY_UPSTREAM
        assert job_id not in NIGHTLY_UPSTREAM
        assert job_id not in CATCHUP_JOBS

    def test_universe_validation_job_function_is_wired(self):
        definition = {d[0]: d for d in JOB_DEFINITIONS}["universe_validation"]
        from trading_signals.scheduler import jobs

        assert definition[1] is jobs.run_universe_validation

    def test_every_job_has_a_registry_entry(self):
        for job_id, *_ in JOB_DEFINITIONS:
            assert job_id in JOB_COLLECTOR_NAMES, job_id

    def test_job_runner_wires_collector(self):
        from unittest.mock import patch

        from trading_signals.scheduler import jobs

        with patch.object(jobs, "_run_collector") as run:
            jobs.run_short_interest_positions_collector()

        job_id, factory = run.call_args.args
        assert job_id == "short_interest_positions_collector"
        assert factory.__name__ == "ShortInterestPositionsCollector"
        assert run.call_args.kwargs == {"skip_on_value_error": True}
