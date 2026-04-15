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


def run_form4_collector() -> None:
    """Daily SEC Form 4 insider trades collection + cluster computation.

    Scheduled for 23:30 Europe/Berlin (after ARK, to spread API load).
    SEC filings are available ~2 business days after transactions.
    """
    from trading_signals.collectors.form4_collector import Form4Collector
    from trading_signals.db.session import get_session
    from trading_signals.derived.insider_clusters import InsiderClusterComputer

    logger.info("Scheduler triggered: form4_collector_job")

    # Step 1: Collect Form 4 filings
    collector = Form4Collector(lookback_days=7)
    log = collector.run()
    logger.info(
        f"form4_collector_job collect: status={log.status}, "
        f"written={log.records_written}"
    )

    # Step 2: Compute insider clusters (only if collection succeeded)
    if log.status == "success":
        with get_session() as session:
            computer = InsiderClusterComputer(session)
            clusters = computer.compute_new()
            logger.info(
                f"form4_collector_job clusters: {clusters} records computed"
            )


def run_form13f_collector() -> None:
    """Weekly SEC Form 13F institutional holdings collection.

    Scheduled for Sundays at 10:00 Europe/Berlin.
    13F filings are quarterly – weekly check catches new filings promptly.
    """
    from trading_signals.collectors.form13f_collector import Form13FCollector

    logger.info("Scheduler triggered: form13f_collector_job")

    collector = Form13FCollector(lookback_days=90)
    log = collector.run()
    logger.info(
        f"form13f_collector_job finished: status={log.status}, "
        f"written={log.records_written}"
    )


def run_politician_trades_collector() -> None:
    """Weekly politician trades collection from official disclosure portals.

    Scheduled for Sundays at 11:00 Europe/Berlin (after Form 13F).
    Politician trades are 30-45 days delayed, weekly check is sufficient.
    """
    from trading_signals.collectors.politician_trades_collector import (
        PoliticianTradesCollector,
    )

    logger.info("Scheduler triggered: politician_trades_collector_job")

    collector = PoliticianTradesCollector(lookback_days=365)
    log = collector.run()
    logger.info(
        f"politician_trades_collector_job finished: status={log.status}, "
        f"written={log.records_written}"
    )


def run_fundamentals_collector() -> None:
    """Weekly fundamentals collection via yfinance.

    Scheduled for Sundays at 01:00 Europe/Berlin (night slot).
    Fetches P/E, margins, revenue growth, EPS, etc. for all active tickers.
    """
    from trading_signals.collectors.fundamentals_collector import (
        FundamentalsCollectorYF,
    )

    logger.info("Scheduler triggered: fundamentals_collector_job")

    collector = FundamentalsCollectorYF()
    log = collector.run()
    logger.info(
        f"fundamentals_collector_job finished: status={log.status}, "
        f"written={log.records_written}"
    )


def run_analyst_ratings_collector() -> None:
    """Daily analyst ratings collection via yfinance.

    Scheduled for 01:00 Europe/Berlin (night slot, after daily collectors).
    Fetches analyst upgrades/downgrades for the last 30 days.
    """
    from trading_signals.collectors.analyst_ratings_collector import (
        AnalystRatingsCollector,
    )

    logger.info("Scheduler triggered: analyst_ratings_collector_job")

    collector = AnalystRatingsCollector(lookback_days=30)
    log = collector.run()
    logger.info(
        f"analyst_ratings_collector_job finished: status={log.status}, "
        f"written={log.records_written}"
    )


def run_earnings_calendar_collector() -> None:
    """Weekly earnings calendar update via yfinance.

    Scheduled for Sundays at 02:00 Europe/Berlin (after fundamentals).
    Fetches past and upcoming earnings dates with EPS surprise data.
    """
    from trading_signals.collectors.earnings_calendar_collector import (
        EarningsCalendarCollector,
    )

    logger.info("Scheduler triggered: earnings_calendar_collector_job")

    collector = EarningsCalendarCollector(earnings_limit=4)
    log = collector.run()
    logger.info(
        f"earnings_calendar_collector_job finished: status={log.status}, "
        f"written={log.records_written}"
    )


def run_technical_indicators_computer() -> None:
    """Daily technical indicators computation.

    Scheduled for 22:30 Europe/Berlin (after Price Collector at 22:15).
    Computes SMA, EMA, RSI, MACD, Bollinger, ATR, Volume SMA,
    and Relative Strength vs SPY from prices_daily data.

    Uses catch-up logic: automatically detects and fills any gaps
    between the latest computed indicator date and the latest price
    date. This handles missed runs, container restarts, and weekends.
    """
    from trading_signals.db.session import get_session
    from trading_signals.derived.technical_indicators import (
        TechnicalIndicatorsComputer,
    )

    logger.info("Scheduler triggered: technical_indicators_computer_job")

    with get_session() as session:
        computer = TechnicalIndicatorsComputer(session)
        written = computer.compute_catchup()
        logger.info(
            f"technical_indicators_computer_job finished: "
            f"{written} records computed"
        )


def run_index_sync() -> None:
    """Monthly index membership sync + sector enrichment.

    Scheduled for 1st of each month at 03:00 Europe/Berlin.
    Updates S&P 500 / Nasdaq 100 membership from Wikipedia,
    validates new tickers against Alpaca, adds them to the
    universe, and enriches any tickers missing sector data.
    """
    from sqlalchemy import select, update

    from trading_signals.collectors.yfinance_client import YFinanceClient
    from trading_signals.db.models.universe import Universe
    from trading_signals.db.session import get_session
    from trading_signals.universe.index_sync import IndexSyncer

    logger.info("Scheduler triggered: index_sync_job")

    with get_session() as session:
        syncer = IndexSyncer(session)
        result = syncer.sync()
        session.commit()
        logger.info(
            f"index_sync_job finished: "
            f"S&P 500={result.sp500_count}, Nasdaq 100={result.nasdaq100_count}, "
            f"added={result.newly_added}, updated={result.membership_updated}"
        )
        if result.new_tickers:
            logger.info(
                f"index_sync_job new tickers: {', '.join(result.new_tickers)}"
            )

    # Step 2: Enrich tickers missing sector/industry data
    with get_session() as session:
        stmt = (
            select(Universe.ticker)
            .where(Universe.is_active.is_(True))
            .where(
                (Universe.sector.is_(None)) | (Universe.sector == "")
            )
            .order_by(Universe.ticker)
        )
        missing = [row[0] for row in session.execute(stmt).all()]

    if missing:
        logger.info(
            f"index_sync_job: enriching {len(missing)} tickers "
            f"with sector/industry from yfinance"
        )
        client = YFinanceClient(
            batch_size=50,
            delay_between_tickers=0.5,
            delay_between_batches=3.0,
        )
        results = client.fetch_sector_info(missing)

        enriched = 0
        deactivated_etfs: list[str] = []

        with get_session() as session:
            from trading_signals.universe.blacklist import add_to_blacklist

            for record in results:
                ticker = record["ticker"]
                quote_type = record.get("quote_type", "")

                # Learned ETF filter: blacklist + deactivate non-equity tickers
                if quote_type and quote_type.upper() != "EQUITY":
                    add_to_blacklist(
                        session, ticker,
                        quote_type=quote_type,
                        source="index_sync",
                    )
                    session.execute(
                        update(Universe)
                        .where(Universe.ticker == ticker)
                        .values(is_active=False)
                    )
                    deactivated_etfs.append(ticker)
                    logger.warning(
                        f"index_sync_job: blacklisted + deactivated {ticker}: "
                        f"quoteType={quote_type}"
                    )
                    continue

                session.execute(
                    update(Universe)
                    .where(Universe.ticker == ticker)
                    .values(
                        sector=record.get("sector"),
                        industry=record.get("industry"),
                    )
                )
                enriched += 1

        if deactivated_etfs:
            logger.info(
                f"index_sync_job: blacklisted {len(deactivated_etfs)} "
                f"non-equity tickers: {deactivated_etfs}"
            )

        logger.info(
            f"index_sync_job sector enrichment: "
            f"{enriched}/{len(missing)} tickers enriched"
        )
    else:
        logger.info("index_sync_job: all tickers have sector data")
