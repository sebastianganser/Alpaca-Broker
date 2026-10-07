"""APScheduler setup: job registration (times are Europe/Berlin).

Nightly timeline (US close = 22:00 CET, 21:00 during DST-mismatch weeks)::

    22:30 price_collector            23:15 options_iv_collector
    22:50 technical_indicators       23:30 form4_collector
    23:00 ark_holdings               00:00 news_collector
    00:15 short_interest_collector (~2.5 h, FINRA data ready ~00:00)
    00:30 sentiment_computer         01:00 analyst_ratings_collector
    01:30 estimates_collector        03:30 log_retention
    04:15 fred_collector
    04:30 nightly_chain: TA catch-up → feature_pipeline → target_backfill
                         → stage2_review → context_pack (waits for running upstream jobs)
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from trading_signals.scheduler import jobs
from trading_signals.scheduler.registry import CHAIN_STEP_JOB_IDS

TIMEZONE = "Europe/Berlin"

# (job_id, function, cron kwargs, name)
JOB_DEFINITIONS: list[tuple[str, object, dict, str]] = [
    # ── Evening slot (after US close) ──
    (
        "price_collector",
        jobs.run_price_collector,
        {"hour": 22, "minute": 30},
        "Daily OHLCV Price Collector (Alpaca)",
    ),
    (
        "technical_indicators_computer",
        jobs.run_technical_indicators_computer,
        {"hour": 22, "minute": 50},
        "Daily Technical Indicators Computation",
    ),
    (
        "ark_holdings",
        jobs.run_ark_holdings_collector,
        {"hour": 23, "minute": 0},
        "Daily ARK ETF Holdings Snapshot",
    ),
    (
        "options_iv_collector",
        jobs.run_options_iv_collector,
        {"hour": 23, "minute": 15},
        "Daily Options IV Snapshots (ATM IV, Skew, Term Structure)",
    ),
    (
        "form4_collector",
        jobs.run_form4_collector,
        {"hour": 23, "minute": 30},
        "Daily SEC Form 4 Insider Trades",
    ),
    # ── Night slot ──
    (
        "news_collector",
        jobs.run_news_collector,
        {"hour": 0, "minute": 0},
        "Daily News Collector (Alpaca News API)",
    ),
    (
        "short_interest_collector",
        jobs.run_short_interest_collector,
        {"hour": 0, "minute": 15},
        "Daily Short Interest/Volume (Massive API)",
    ),
    (
        "sentiment_computer",
        jobs.run_sentiment_computer,
        {"hour": 0, "minute": 30},
        "Daily Sentiment Scoring (FinBERT)",
    ),
    (
        "analyst_ratings_collector",
        jobs.run_analyst_ratings_collector,
        {"hour": 1, "minute": 0},
        "Daily Analyst Ratings (yfinance)",
    ),
    # CRITICAL: Rolling 90-day window – must run daily without fail.
    (
        "estimates_collector",
        jobs.run_estimates_collector,
        {"hour": 1, "minute": 30},
        "Daily Estimates Collector (EPS/Revenue Consensus + Revisions)",
    ),
    (
        "log_retention",
        jobs.run_log_retention,
        {"hour": 3, "minute": 30},
        "Daily Log Retention (90 days)",
    ),
    # FRED updates ~22:00 ET = 04:00 CET. 6 series, ~2s total.
    (
        "fred_collector",
        jobs.run_fred_collector,
        {"hour": 4, "minute": 15},
        "Daily FRED Macro Indicators (VIX, Yields, HY, Dollar, Inflation)",
    ),
    (
        "nightly_chain",
        jobs.run_nightly_chain,
        {"hour": 4, "minute": 30},
        "Nightly Chain (TA → Features → Targets → Context Pack)",
    ),
    # ── Weekly (Sunday) ──
    (
        "fundamentals_collector",
        jobs.run_fundamentals_collector,
        {"day_of_week": "sun", "hour": 1, "minute": 0},
        "Weekly Fundamentals (yfinance)",
    ),
    (
        "earnings_calendar_collector",
        jobs.run_earnings_calendar_collector,
        {"day_of_week": "sun", "hour": 2, "minute": 0},
        "Weekly Earnings Calendar (yfinance)",
    ),
    (
        "data_retention",
        jobs.run_data_retention,
        {"day_of_week": "sun", "hour": 3, "minute": 0},
        "Weekly Data Retention (20 quarters, excl. earnings_calendar)",
    ),
    (
        "form13f_collector",
        jobs.run_form13f_collector,
        {"day_of_week": "sun", "hour": 10, "minute": 0},
        "Weekly SEC Form 13F Institutional Holdings",
    ),
    (
        "politician_trades_collector",
        jobs.run_politician_trades_collector,
        {"day_of_week": "sun", "hour": 11, "minute": 0},
        "Weekly Politician Trades (Senate eFD)",
    ),
    # ── Monthly (1st) ──
    (
        "index_sync",
        jobs.run_index_sync,
        {"day": 1, "hour": 3, "minute": 0},
        "Monthly Index Membership Sync (S&P 500 + Nasdaq 100)",
    ),
    (
        "feature_analysis",
        jobs.run_feature_analysis,
        {"day": 1, "hour": 7, "minute": 0},
        "Monthly Feature Analysis (Correlations + ML Importance)",
    ),
    # ── Chain steps: paused, executed by nightly_chain or manual trigger ──
    (
        "feature_pipeline",
        jobs.run_feature_pipeline,
        {"hour": 4, "minute": 30},
        "Feature Pipeline (Snapshot Computation) – via Nightly Chain",
    ),
    (
        "target_backfill",
        jobs.run_target_backfill,
        {"hour": 4, "minute": 30},
        "Target Backfill (Forward Returns) – via Nightly Chain",
    ),
    (
        "stage2_review",
        jobs.run_stage2_review,
        {"hour": 4, "minute": 30},
        "Stufe-2-Vorwärtstest (decisions.yaml einlesen + Auswertung) – via Nightly Chain",
    ),
    (
        "context_pack_generator",
        jobs.run_context_pack_generator,
        {"hour": 4, "minute": 30},
        "Context Pack (Top Candidates + Features) – via Nightly Chain",
    ),
]


def create_scheduler() -> BackgroundScheduler:
    """Create and configure the APScheduler instance.

    Uses BackgroundScheduler (non-blocking) so FastAPI can run
    as the main process while jobs execute in background threads.
    Chain steps are added *paused* (``next_run_time=None``): they are listed
    in the UI and can be triggered manually, but only run on their own as
    part of ``nightly_chain``.
    """
    scheduler = BackgroundScheduler(
        timezone=TIMEZONE,
        job_defaults={
            "coalesce": True,  # Merge missed runs into one
            "max_instances": 1,  # Only one instance per job at a time
            "misfire_grace_time": 3600,  # 1 hour grace time for misfires
        },
    )
    for job_id, func, cron, name in JOB_DEFINITIONS:
        kwargs = {"next_run_time": None} if job_id in CHAIN_STEP_JOB_IDS else {}
        scheduler.add_job(
            func,
            CronTrigger(timezone=TIMEZONE, **cron),
            id=job_id,
            name=name,
            **kwargs,
        )
    return scheduler
