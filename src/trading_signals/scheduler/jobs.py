"""Scheduler job definitions.

Each job is a simple function that instantiates a collector and runs it.
Jobs are registered with APScheduler using CronTrigger.
"""

from trading_signals.collectors.prices_alpaca import PriceCollectorAlpaca
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)


def run_price_collector() -> None:
    """Daily price collection job.

    Scheduled for 22:15 Europe/Berlin (after US market close at 22:00 MEZ).
    Uses Alpaca Market Data API (replaced yfinance in Sprint 1b).
    """
    logger.info("Scheduler triggered: price_collector_job")
    collector = PriceCollectorAlpaca(lookback_days=10)
    log = collector.run()
    logger.info(
        f"price_collector_job finished: status={log.status}, "
        f"written={log.records_written}"
    )


def run_ark_holdings_collector() -> None:
    """Daily ARK holdings snapshot + delta computation.

    Scheduled for 23:00 Europe/Berlin (ARK publishes after US close,
    arkfunds.io needs time to aggregate).
    """
    from trading_signals.collectors.ark_holdings import ARKHoldingsCollector
    from trading_signals.db.session import get_session
    from trading_signals.derived.ark_deltas import ARKDeltaComputer

    logger.info("Scheduler triggered: ark_holdings_job")

    # Step 1: Collect holdings
    collector = ARKHoldingsCollector()
    log = collector.run()
    logger.info(
        f"ark_holdings_job collect: status={log.status}, "
        f"written={log.records_written}"
    )

    # Step 2: Compute deltas (only if collection succeeded)
    if log.status == "success":
        with get_session() as session:
            computer = ARKDeltaComputer(session)
            deltas = computer.compute_all()
            logger.info(f"ark_holdings_job deltas: {deltas} records computed")

