"""Scheduler job definitions.

Each job is a simple function that instantiates a collector and runs it.
Jobs are registered with APScheduler using CronTrigger.
"""

from trading_signals.collectors.prices_yfinance import PriceCollectorYFinance
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


def run_price_collector() -> None:
    """Daily price collection job.

    Scheduled for 22:15 Europe/Berlin (after US market close at 22:00 MEZ).
    """
    logger.info("Scheduler triggered: price_collector_job")
    collector = PriceCollectorYFinance(period="10d")
    log = collector.run()
    logger.info(
        f"price_collector_job finished: status={log.status}, "
        f"written={log.records_written}"
    )
