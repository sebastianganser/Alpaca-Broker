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

    collector = Form13FCollector(lookback_days=120)
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


def run_estimates_collector() -> None:
    """Daily estimates collection via yfinance.

    Scheduled for 01:30 Europe/Berlin (night slot, after analyst ratings).
    Fetches EPS/Revenue consensus, revisions, and trend data.

    CRITICAL: Yahoo provides a rolling 90-day window for EPS revisions.
    Every day this collector doesn't run, one day of irrecoverable
    revision history is permanently lost.
    """
    from trading_signals.collectors.estimates_collector import (
        EstimatesCollector,
    )

    logger.info("Scheduler triggered: estimates_collector_job")

    collector = EstimatesCollector()
    log = collector.run()
    logger.info(
        f"estimates_collector_job finished: status={log.status}, "
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
    from datetime import datetime

    from trading_signals.utils.logging import CollectorLogCapture
    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session
    from trading_signals.derived.technical_indicators import (
        TechnicalIndicatorsComputer,
    )

    collector_name = "technical_indicators"
    logger.info(f"Scheduler triggered: {collector_name}_job")

    started_at = datetime.now()

    with CollectorLogCapture(collector_name) as log_capture:
        try:
            with get_session() as session:
                computer = TechnicalIndicatorsComputer(session)
                written = computer.compute_catchup()

                # Write collection_log entry
                log_entry = CollectionLog(
                    collector_name=collector_name,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    status="success",
                    records_fetched=written,
                    records_written=written,
                    gaps_detected=0,
                    log_lines=log_capture.get_lines(),
                )
                session.add(log_entry)
                session.commit()

            logger.info(
                f"{collector_name}_job finished: "
                f"{written} records computed"
            )
        except Exception as e:
            logger.error(f"{collector_name}_job FAILED: {e}")
            try:
                with get_session() as session:
                    log_entry = CollectionLog(
                        collector_name=collector_name,
                        started_at=started_at,
                        finished_at=datetime.now(),
                        status="error",
                        records_fetched=0,
                        records_written=0,
                        gaps_detected=0,
                        notes=str(e)[:2000],
                        log_lines=log_capture.get_lines(),
                    )
                    session.add(log_entry)
                    session.commit()
            except Exception:
                logger.error(f"{collector_name}_job: Failed to write error log")


def run_index_sync() -> None:
    """Monthly index membership sync + sector enrichment.

    Scheduled for 1st of each month at 03:00 Europe/Berlin.
    Updates S&P 500 / Nasdaq 100 membership from Wikipedia,
    validates new tickers against Alpaca, adds them to the
    universe, and enriches any tickers missing sector data.
    """
    from datetime import datetime

    from sqlalchemy import select, update

    from trading_signals.collectors.yfinance_client import YFinanceClient
    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.models.universe import Universe
    from trading_signals.db.session import get_session
    from trading_signals.universe.index_sync import IndexSyncer
    from trading_signals.utils.logging import CollectorLogCapture

    collector_name = "index_sync"
    logger.info(f"Scheduler triggered: {collector_name}_job")

    started_at = datetime.now()
    records_written = 0

    with CollectorLogCapture(collector_name) as log_capture:
        try:
            # Step 1: Sync index membership
            with get_session() as session:
                syncer = IndexSyncer(session)
                result = syncer.sync()
                session.commit()
                records_written += result.newly_added + result.membership_updated
                logger.info(
                    f"{collector_name}_job finished: "
                    f"S&P 500={result.sp500_count}, Nasdaq 100={result.nasdaq100_count}, "
                    f"added={result.newly_added}, updated={result.membership_updated}"
                )
                if result.new_tickers:
                    logger.info(
                        f"{collector_name}_job new tickers: {', '.join(result.new_tickers)}"
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
                    f"{collector_name}_job: enriching {len(missing)} tickers "
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
                                f"{collector_name}_job: blacklisted + deactivated {ticker}: "
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
                    records_written += enriched

                if deactivated_etfs:
                    logger.info(
                        f"{collector_name}_job: blacklisted {len(deactivated_etfs)} "
                        f"non-equity tickers: {deactivated_etfs}"
                    )

                logger.info(
                    f"{collector_name}_job sector enrichment: "
                    f"{enriched}/{len(missing)} tickers enriched"
                )
            else:
                logger.info(f"{collector_name}_job: all tickers have sector data")

            # Write success log entry
            with get_session() as session:
                log_entry = CollectionLog(
                    collector_name=collector_name,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    status="success",
                    records_fetched=records_written,
                    records_written=records_written,
                    gaps_detected=0,
                    log_lines=log_capture.get_lines(),
                )
                session.add(log_entry)
                session.commit()

            logger.info(
                f"{collector_name}_job completed: "
                f"{records_written} records written"
            )
        except Exception as e:
            logger.error(f"{collector_name}_job FAILED: {e}")
            try:
                with get_session() as session:
                    log_entry = CollectionLog(
                        collector_name=collector_name,
                        started_at=started_at,
                        finished_at=datetime.now(),
                        status="failed",
                        records_fetched=0,
                        records_written=0,
                        gaps_detected=0,
                        notes=str(e)[:2000],
                        log_lines=log_capture.get_lines(),
                    )
                    session.add(log_entry)
                    session.commit()
            except Exception:
                logger.error(f"{collector_name}_job: Failed to write error log")


def run_feature_pipeline() -> None:
    """Daily feature pipeline – computes feature snapshots for all tickers.

    Scheduled for 02:00 Europe/Berlin (night slot, after all collectors).
    Aggregates all raw + derived signals into feature_snapshots table.
    """
    from datetime import datetime, date, timedelta

    from trading_signals.utils.logging import CollectorLogCapture
    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session
    from trading_signals.derived.feature_pipeline import FeaturePipeline

    collector_name = "feature_pipeline"
    logger.info(f"Scheduler triggered: {collector_name}_job")

    started_at = datetime.now()
    # Default: compute for yesterday (latest complete trading day)
    target_date = date.today() - timedelta(days=1)

    with CollectorLogCapture(collector_name) as log_capture:
        try:
            with get_session() as session:
                pipeline = FeaturePipeline(session)
                written = pipeline.compute_daily(target_date)

                log_entry = CollectionLog(
                    collector_name=collector_name,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    status="success",
                    records_fetched=written,
                    records_written=written,
                    gaps_detected=0,
                    log_lines=log_capture.get_lines(),
                )
                session.add(log_entry)
                session.commit()

            logger.info(
                f"{collector_name}_job finished: "
                f"{written} snapshots computed for {target_date}"
            )
        except Exception as e:
            logger.error(f"{collector_name}_job FAILED: {e}")
            try:
                with get_session() as session:
                    log_entry = CollectionLog(
                        collector_name=collector_name,
                        started_at=started_at,
                        finished_at=datetime.now(),
                        status="error",
                        records_fetched=0,
                        records_written=0,
                        gaps_detected=0,
                        notes=str(e)[:2000],
                        log_lines=log_capture.get_lines(),
                    )
                    session.add(log_entry)
                    session.commit()
            except Exception:
                logger.error(f"{collector_name}_job: Failed to write error log")


def run_target_backfill() -> None:
    """Daily target backfill – fills forward returns retrospectively.

    Scheduled for 02:15 Europe/Berlin (after feature pipeline).
    Computes return_1d/5d/20d/60d for feature snapshots where
    sufficient future price data now exists.
    """
    from datetime import datetime

    from trading_signals.utils.logging import CollectorLogCapture
    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session
    from trading_signals.derived.target_backfill import TargetBackfillComputer

    collector_name = "target_backfill"
    logger.info(f"Scheduler triggered: {collector_name}_job")

    started_at = datetime.now()

    with CollectorLogCapture(collector_name) as log_capture:
        try:
            with get_session() as session:
                computer = TargetBackfillComputer(session)
                written = computer.backfill_all()

                log_entry = CollectionLog(
                    collector_name=collector_name,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    status="success",
                    records_fetched=written,
                    records_written=written,
                    gaps_detected=0,
                    log_lines=log_capture.get_lines(),
                )
                session.add(log_entry)
                session.commit()

            logger.info(
                f"{collector_name}_job finished: "
                f"{written} return values backfilled"
            )
        except Exception as e:
            logger.error(f"{collector_name}_job FAILED: {e}")
            try:
                with get_session() as session:
                    log_entry = CollectionLog(
                        collector_name=collector_name,
                        started_at=started_at,
                        finished_at=datetime.now(),
                        status="error",
                        records_fetched=0,
                        records_written=0,
                        gaps_detected=0,
                        notes=str(e)[:2000],
                        log_lines=log_capture.get_lines(),
                    )
                    session.add(log_entry)
                    session.commit()
            except Exception:
                logger.error(f"{collector_name}_job: Failed to write error log")


def run_news_collector() -> None:
    """Daily news collection from Alpaca News API.

    Scheduled for 00:00 Europe/Berlin (night slot).
    Fetches ticker-specific + global market news from the last 36 hours.
    """
    from trading_signals.collectors.news_collector import NewsCollectorAlpaca

    logger.info("Scheduler triggered: news_collector_job")

    collector = NewsCollectorAlpaca(lookback_hours=36)
    log = collector.run()
    logger.info(
        f"news_collector_job finished: status={log.status}, "
        f"written={log.records_written}"
    )


def run_sentiment_computer() -> None:
    """Daily sentiment scoring of unscored news articles.

    Scheduled for 00:30 Europe/Berlin (after news collector).
    Uses FinBERT (CPU) to score all articles not yet processed.
    """
    from datetime import datetime

    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session
    from trading_signals.derived.sentiment_computer import SentimentComputer
    from trading_signals.derived.sentiment_scorer import FinBERTScorer
    from trading_signals.utils.logging import CollectorLogCapture

    collector_name = "sentiment_computer"
    logger.info(f"Scheduler triggered: {collector_name}_job")

    started_at = datetime.now()

    with CollectorLogCapture(collector_name) as log_capture:
        try:
            scorer = FinBERTScorer(batch_size=32)

            with get_session() as session:
                computer = SentimentComputer(session, scorer)
                written = computer.compute()

                log_entry = CollectionLog(
                    collector_name=collector_name,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    status="success",
                    records_fetched=written,
                    records_written=written,
                    gaps_detected=0,
                    log_lines=log_capture.get_lines(),
                )
                session.add(log_entry)
                session.commit()

            logger.info(
                f"{collector_name}_job finished: "
                f"{written} sentiment scores computed"
            )
        except Exception as e:
            logger.error(f"{collector_name}_job FAILED: {e}")
            try:
                with get_session() as session:
                    log_entry = CollectionLog(
                        collector_name=collector_name,
                        started_at=started_at,
                        finished_at=datetime.now(),
                        status="error",
                        records_fetched=0,
                        records_written=0,
                        gaps_detected=0,
                        notes=str(e)[:2000],
                        log_lines=log_capture.get_lines(),
                    )
                    session.add(log_entry)
                    session.commit()
            except Exception:
                logger.error(f"{collector_name}_job: Failed to write error log")


def run_fred_collector() -> None:
    """Daily FRED macro indicator collection.

    Scheduled for 04:15 Europe/Berlin (FRED updates ~22:00 ET = 04:00 CET).
    Fetches 6 macro series: VIX, Treasury yields, HY spread,
    Dollar index, Breakeven Inflation.
    Very lightweight: 6 API calls, ~2 seconds total.
    """
    from trading_signals.collectors.fred_collector import FredCollector

    logger.info("Scheduler triggered: fred_collector_job")

    try:
        collector = FredCollector()
        log = collector.run()
        logger.info(
            f"fred_collector_job finished: status={log.status}, "
            f"written={log.records_written}"
        )
    except ValueError as e:
        # FRED_API_KEY not configured
        logger.warning(f"fred_collector_job skipped: {e}")


# ── Options IV (Sprint 9.5b D3) ─────────────────────────────────────────

def run_options_iv_collector() -> None:
    """Daily options IV snapshot collection.

    Scheduled for 04:30 Europe/Berlin (after market close + FRED).
    Fetches ATM implied volatility, skew, term structure, and OI
    for all universe tickers via Alpaca Options API.

    Rate-limited: ~170 req/min, ~750 tickers ≈ 5-6 minutes.
    IV-Rank needs ~1 year of daily data to be meaningful.
    """
    from trading_signals.collectors.options_iv_collector import OptionsIVCollector

    logger.info("Scheduler triggered: options_iv_collector_job")

    try:
        collector = OptionsIVCollector()
        log = collector.run()
        logger.info(
            f"options_iv_collector_job finished: status={log.status}, "
            f"written={log.records_written}"
        )
    except ValueError as e:
        logger.warning(f"options_iv_collector_job skipped: {e}")


# ── Log Retention ────────────────────────────────────────────────────────

LOG_RETENTION_DAYS = 90


def run_log_retention() -> None:
    """Delete collection_logs older than LOG_RETENTION_DAYS.

    Scheduled for 03:30 Europe/Berlin (daily, after all collectors).
    Keeps the database lean by pruning old log entries.
    """
    from datetime import datetime, timedelta

    from sqlalchemy import delete

    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session

    cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
    logger.info(
        f"Scheduler triggered: log_retention_job "
        f"(deleting logs older than {cutoff.date()})"
    )

    try:
        with get_session() as session:
            result = session.execute(
                delete(CollectionLog)
                .where(CollectionLog.started_at < cutoff)
            )
            deleted = result.rowcount
            session.commit()

        logger.info(f"log_retention_job finished: {deleted} old log entries deleted")
    except Exception as e:
        logger.error(f"log_retention_job FAILED: {e}")


# ── Feature Analysis ─────────────────────────────────────────────────────


def run_feature_analysis() -> None:
    """Monthly feature analysis – correlations, importance, hypothesis tests.

    Scheduled for 1st of each month at 05:00 Europe/Berlin.
    Runs Spearman correlations, Random Forest + LASSO feature importance,
    and hypothesis tests (H1–H13). Stores structured results in
    analysis_reports table and generates an HTML report.

    CPU-intensive (~2–5 min depending on data volume).
    """
    from datetime import datetime

    from trading_signals.utils.logging import CollectorLogCapture
    from trading_signals.db.models.collection_log import CollectionLog
    from trading_signals.db.session import get_session

    collector_name = "feature_analysis"
    logger.info(f"Scheduler triggered: {collector_name}_job")

    started_at = datetime.now()

    with CollectorLogCapture(collector_name) as log_capture:
        try:
            from trading_signals.analysis.feature_report import (
                FeatureAnalysisEngine,
            )

            with get_session() as session:
                engine = FeatureAnalysisEngine(session)
                report = engine.run()

                # Extract values while still in session context
                snap_count = report.snapshot_count
                tick_count = report.ticker_count
                comp_time = report.computation_time_seconds

                log_entry = CollectionLog(
                    collector_name=collector_name,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    status="success",
                    records_fetched=snap_count,
                    records_written=1,
                    gaps_detected=0,
                    log_lines=log_capture.get_lines(),
                )
                session.add(log_entry)
                session.commit()

            logger.info(
                f"{collector_name}_job finished: "
                f"{snap_count} snapshots analyzed, "
                f"{tick_count} tickers, "
                f"{comp_time:.0f}s"
            )
        except Exception as e:
            logger.error(f"{collector_name}_job FAILED: {e}")
            try:
                with get_session() as session:
                    log_entry = CollectionLog(
                        collector_name=collector_name,
                        started_at=started_at,
                        finished_at=datetime.now(),
                        status="error",
                        records_fetched=0,
                        records_written=0,
                        gaps_detected=0,
                        notes=str(e)[:2000],
                        log_lines=log_capture.get_lines(),
                    )
                    session.add(log_entry)
                    session.commit()
            except Exception:
                logger.error(f"{collector_name}_job: Failed to write error log")

