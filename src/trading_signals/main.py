"""Trading Signals Collector – Main Entrypoint.

Starts the APScheduler with all configured jobs.
Designed to run as a long-lived process inside the Docker container.

Usage:
    uv run python -m trading_signals.main
"""

import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from trading_signals.scheduler.jobs import run_price_collector
from trading_signals.utils.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


def create_scheduler() -> BlockingScheduler:
    """Create and configure the APScheduler instance."""
    scheduler = BlockingScheduler(
        timezone="Europe/Berlin",
        job_defaults={
            "coalesce": True,       # Merge missed runs into one
            "max_instances": 1,     # Only one instance per job at a time
            "misfire_grace_time": 3600,  # 1 hour grace time for misfires
        },
    )

    # ── Price Collector: Daily at 22:15 (after US market close) ──
    scheduler.add_job(
        run_price_collector,
        CronTrigger(hour=22, minute=15),
        id="price_collector",
        name="Daily OHLCV Price Collector (yfinance)",
    )

    return scheduler


def main() -> None:
    """Start the collector scheduler."""
    logger.info("=" * 60)
    logger.info("Trading Signals Collector starting...")
    logger.info("=" * 60)

    scheduler = create_scheduler()

    # Log all registered jobs
    for job in scheduler.get_jobs():
        logger.info(f"  Job: {job.name}")
        logger.info(f"    Trigger: {job.trigger}")
        logger.info(f"    Next run: {job.next_run_time}")

    # Graceful shutdown on SIGTERM/SIGINT
    def shutdown(signum, frame):
        logger.info("Shutdown signal received, stopping scheduler...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("Scheduler started. Waiting for jobs...")
    scheduler.start()


if __name__ == "__main__":
    main()
