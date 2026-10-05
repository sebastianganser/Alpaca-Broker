"""Tests for scheduler setup and the job/collector registry."""

from trading_signals.scheduler.registry import (
    CATCHUP_JOBS,
    CHAIN_STEP_JOB_IDS,
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
