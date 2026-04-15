"""NewTickerOnboarder – auto-expands universe and backfills new tickers.

When signal collectors (ARK, Politician Trades) discover tickers that
are not yet in our universe, this service:
  1. Validates them against Alpaca (only tradeable tickers)
  2. Adds them to the universe via UniverseManager
  3. Backfills historical prices from Alpaca (~4 years)
  4. Computes TA indicators from the backfilled prices
  5. Fetches a fundamentals snapshot from yfinance
  6. Enriches sector/industry data from yfinance

This runs synchronously within the collector's thread – at 1-5 new
tickers per run, the overhead is ~30-60 seconds, acceptable for
night-scheduled collectors.
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.db.models.universe import Universe
from trading_signals.universe.alpaca_validator import AlpacaAssetValidator
from trading_signals.universe.manager import UniverseManager
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

# How far back to fetch prices for newly discovered tickers
BACKFILL_LOOKBACK_DAYS = 4 * 365  # ~4 years


class NewTickerOnboarder:
    """Discovers new tickers from signal data and fully onboards them.

    Usage from any collector's store() method:

        onboarder = NewTickerOnboarder(session)
        new_tickers = onboarder.onboard(
            tickers={"SIRI", "PLTR", "AAPL"},
            source="politician_trades",
        )
        # new_tickers = ["SIRI"]  (PLTR, AAPL already existed)
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.manager = UniverseManager(session)

    def onboard(
        self,
        tickers: set[str],
        source: str,
    ) -> list[str]:
        """Check tickers against universe, add new ones, trigger backfill.

        Args:
            tickers: Set of ticker symbols discovered by a collector.
            source: Identifier for the collector (e.g., 'politician_trades',
                    'ark_etf', 'form4').

        Returns:
            List of newly added ticker symbols.
        """
        if not tickers:
            return []

        # Step 1: Find tickers not yet in universe
        all_stmt = select(Universe.ticker)
        all_existing = {row[0] for row in self.session.execute(all_stmt).all()}

        new_tickers = tickers - all_existing
        if not new_tickers:
            return []

        logger.info(
            f"[onboarder] Found {len(new_tickers)} new tickers from "
            f"{source}: {sorted(new_tickers)}"
        )

        # Step 2: Validate against Alpaca (only tradeable tickers)
        try:
            validator = AlpacaAssetValidator()
            alpaca_assets = validator.fetch_all_assets()
        except Exception as e:
            logger.warning(
                f"[onboarder] Alpaca validation failed: {e}. "
                f"Skipping universe expansion."
            )
            return []

        added: list[str] = []
        for ticker in sorted(new_tickers):
            asset = alpaca_assets.get(ticker)
            if asset and asset.tradable:
                self.manager.add_ticker(
                    ticker=ticker,
                    company_name=asset.name or None,
                    added_by=source,
                    exchange=asset.exchange,
                )
                added.append(ticker)
            else:
                reason = "not found" if not asset else "not tradeable"
                logger.debug(
                    f"[onboarder] Skipping {ticker}: {reason} on Alpaca"
                )

        if not added:
            logger.info("[onboarder] No new Alpaca-tradeable tickers to add.")
            return []

        # Commit universe additions so backfill can see them
        self.session.commit()

        logger.info(
            f"[onboarder] Added {len(added)} tickers to universe: {added}. "
            f"Starting auto-backfill..."
        )

        # Step 3: Auto-backfill (prices, TA, fundamentals, sector)
        self._backfill_prices(added)
        self._backfill_indicators(added)
        self._backfill_fundamentals(added)
        self._enrich_sector(added)

        logger.info(
            f"[onboarder] Onboarding complete for {len(added)} tickers: {added}"
        )
        return added

    def _backfill_prices(self, tickers: list[str]) -> None:
        """Fetch historical prices from Alpaca for new tickers."""
        try:
            from trading_signals.collectors.prices_alpaca import (
                BATCH_SIZE,
                _fetch_bars_batch,
                _parse_bar_timestamp,
                PriceCollectorAlpaca,
            )
            from trading_signals.db.models.prices import PriceDaily
            from trading_signals.db.session import get_session

            start_date = (
                date.today() - timedelta(days=BACKFILL_LOOKBACK_DAYS)
            ).isoformat()
            end_date = date.today().isoformat()

            collector = PriceCollectorAlpaca(lookback_days=BACKFILL_LOOKBACK_DAYS)
            total_written = 0

            for i in range(0, len(tickers), BATCH_SIZE):
                batch = tickers[i : i + BATCH_SIZE]

                try:
                    bars = _fetch_bars_batch(
                        symbols=batch,
                        start=start_date,
                        end=end_date,
                        headers=collector._headers,
                    )

                    with get_session() as session:
                        batch_written = 0
                        for ticker, ticker_bars in bars.items():
                            for bar in ticker_bars:
                                close_val = bar.get("c")
                                if close_val is None:
                                    continue
                                trade_date = _parse_bar_timestamp(
                                    bar.get("t", "")
                                )
                                if trade_date is None:
                                    continue

                                stmt = (
                                    pg_insert(PriceDaily)
                                    .values(
                                        ticker=ticker,
                                        trade_date=trade_date,
                                        open=bar.get("o"),
                                        high=bar.get("h"),
                                        low=bar.get("l"),
                                        close=close_val,
                                        adj_close=close_val,
                                        volume=bar.get("v"),
                                        source="alpaca",
                                        is_extrapolated=False,
                                    )
                                    .on_conflict_do_nothing(
                                        index_elements=["ticker", "trade_date"]
                                    )
                                )
                                result = session.execute(stmt)
                                if result.rowcount > 0:
                                    batch_written += 1

                        total_written += batch_written

                except Exception as e:
                    logger.warning(
                        f"[onboarder] Price backfill batch failed: {e}"
                    )

            logger.info(
                f"[onboarder] Price backfill: {total_written} records "
                f"for {len(tickers)} tickers"
            )

        except Exception as e:
            logger.error(f"[onboarder] Price backfill failed: {e}")

    def _backfill_indicators(self, tickers: list[str]) -> None:
        """Compute TA indicators for all backfilled prices."""
        try:
            from trading_signals.db.session import get_session
            from trading_signals.derived.technical_indicators import (
                TechnicalIndicatorsComputer,
            )

            total_written = 0
            with get_session() as session:
                computer = TechnicalIndicatorsComputer(session)
                for ticker in tickers:
                    try:
                        written = computer._compute_backfill(ticker)
                        total_written += written
                    except Exception as e:
                        logger.warning(
                            f"[onboarder] TA backfill {ticker} failed: {e}"
                        )

            logger.info(
                f"[onboarder] TA backfill: {total_written} indicator records "
                f"for {len(tickers)} tickers"
            )

        except Exception as e:
            logger.error(f"[onboarder] TA backfill failed: {e}")

    def _backfill_fundamentals(self, tickers: list[str]) -> None:
        """Fetch fundamentals snapshot for new tickers."""
        try:
            from trading_signals.collectors.fundamentals_collector import (
                FundamentalsCollectorYF,
            )
            from trading_signals.db.session import get_session

            # Use a fresh collector instance but only for our tickers
            collector = FundamentalsCollectorYF()

            # Call the collector's store method with fetched data
            with get_session() as session:
                data = collector.client.fetch_fundamentals(tickers)
                if data:
                    from trading_signals.db.models.fundamentals import (
                        FundamentalsSnapshot,
                    )

                    written = 0
                    for record in data:
                        stmt = (
                            pg_insert(FundamentalsSnapshot)
                            .values(
                                snapshot_date=date.today(),
                                **record,
                            )
                            .on_conflict_do_nothing(
                                index_elements=["ticker", "snapshot_date"]
                            )
                        )
                        result = session.execute(stmt)
                        if result.rowcount > 0:
                            written += 1

                    logger.info(
                        f"[onboarder] Fundamentals: {written}/{len(data)} "
                        f"snapshots stored"
                    )

        except Exception as e:
            logger.error(f"[onboarder] Fundamentals backfill failed: {e}")

    def _enrich_sector(self, tickers: list[str]) -> None:
        """Fetch sector/industry data for new tickers."""
        try:
            from sqlalchemy import update

            from trading_signals.collectors.yfinance_client import YFinanceClient
            from trading_signals.db.session import get_session

            client = YFinanceClient(
                batch_size=50,
                delay_between_tickers=0.5,
                delay_between_batches=3.0,
            )
            results = client.fetch_sector_info(tickers)

            with get_session() as session:
                for record in results:
                    session.execute(
                        update(Universe)
                        .where(Universe.ticker == record["ticker"])
                        .values(
                            sector=record.get("sector"),
                            industry=record.get("industry"),
                        )
                    )

            logger.info(
                f"[onboarder] Sector enrichment: {len(results)}/{len(tickers)} "
                f"tickers enriched"
            )

        except Exception as e:
            logger.error(f"[onboarder] Sector enrichment failed: {e}")
