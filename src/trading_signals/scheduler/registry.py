"""Scheduler job registry: job ids ↔ collection_log collector names.

Single source of truth used by the dashboard, the logs API, health checks,
startup catch-up and the nightly chain. Kept free of heavy imports so API
modules can import it cheaply.
"""

from __future__ import annotations

#: Suffix for one-shot clones of manual-only jobs (see ``ops`` trigger).
MANUAL_SUFFIX = "__manual"

#: APScheduler job id → ``collection_log.collector_name`` written by that job.
JOB_COLLECTOR_NAMES: dict[str, str] = {
    "price_collector": "prices_alpaca",
    "technical_indicators_computer": "technical_indicators",
    "ark_holdings": "ark_holdings",
    "form4_collector": "form4_collector",
    "options_iv_collector": "options_iv_collector",
    "news_collector": "news_alpaca",
    "short_interest_collector": "short_interest_collector",
    "sentiment_computer": "sentiment_computer",
    "analyst_ratings_collector": "analyst_ratings_collector",
    "estimates_collector": "estimates_collector",
    "fred_collector": "fred_collector",
    "nightly_chain": "nightly_chain",
    "feature_pipeline": "feature_pipeline",
    "target_backfill": "target_backfill",
    "context_pack_generator": "context_pack_generator",
    "form13f_collector": "form13f_collector",
    "politician_trades_collector": "politician_trades_collector",
    "fundamentals_collector": "fundamentals_yf",
    "earnings_calendar_collector": "earnings_calendar_collector",
    "index_sync": "index_sync",
    "log_retention": "log_retention",
    "data_retention": "data_retention",
    "feature_analysis": "feature_analysis",
}

#: Steps of the nightly chain. Registered as *paused* jobs so they stay
#: visible/triggerable in the UI but never fire on their own.
CHAIN_STEP_JOB_IDS: tuple[str, ...] = (
    "feature_pipeline",
    "target_backfill",
    "context_pack_generator",
)

#: Collector runs (collector_name) the nightly chain expects to have
#: finished *after* the close of the target session. ``True`` = critical.
NIGHTLY_UPSTREAM: dict[str, bool] = {
    "prices_alpaca": True,
    "options_iv_collector": False,
    "short_interest_collector": False,
    "fred_collector": False,
    "estimates_collector": False,
    "analyst_ratings_collector": False,
    "ark_holdings": False,
    "form4_collector": False,
    "news_alpaca": False,
    "sentiment_computer": False,
}

#: Jobs that are re-run once shortly after startup if their last scheduled
#: run was missed (in-memory job store → APScheduler can't detect misfires
#: across restarts). Value = delay in minutes after startup.
CATCHUP_JOBS: dict[str, int] = {
    "price_collector": 2,
    "estimates_collector": 4,  # rolling 90-day revision window – critical
    "nightly_chain": 15,  # waits for running upstream collectors itself
}


def normalize_job_id(job_id: str) -> str:
    """Strip the manual-run suffix from a job id."""
    if job_id.endswith(MANUAL_SUFFIX):
        return job_id[: -len(MANUAL_SUFFIX)]
    return job_id


def get_collector_name(job_id: str) -> str:
    """Return the collection_log collector_name for a scheduler job id.

    Unknown ids are returned unchanged (they may already be collector names).
    """
    job_id = normalize_job_id(job_id)
    return JOB_COLLECTOR_NAMES.get(job_id, job_id)
