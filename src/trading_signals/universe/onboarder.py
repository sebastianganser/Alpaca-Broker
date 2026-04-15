"""NewTickerOnboarder – auto-expands universe and backfills new tickers.

When signal collectors (ARK, Politician Trades) discover tickers that
are not yet in our universe, this service:
  1. Checks against the ticker blacklist (learned ETF filter)
  2. Validates them against Alpaca (only tradeable tickers)
  3. Verifies quoteType via yfinance (only EQUITY, not ETF/MUTUALFUND)
  4. Adds them to the universe via UniverseManager
  5. Backfills historical prices from Alpaca (~4 years)
  6. Computes TA indicators from the backfilled prices
  7. Fetches a fundamentals snapshot from yfinance
  8. Enriches sector/industry data from yfinance

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

        # Step 2: Filter against blacklist (learned ETF filter)
        from trading_signals.universe.blacklist import filter_blacklisted

        new_tickers, blocked = filter_blacklisted(self.session, new_tickers)
        if blocked:
            logger.info(
                f"[onboarder] Blocked {len(blocked)} blacklisted tickers: "
                f"{sorted(blocked)}"
            )
        if not new_tickers:
            return []

        logger.info(
            f"[onboarder] Found {len(new_tickers)} new tickers from "
            f"{source}: {sorted(new_tickers)}"
        )

        # Step 3: Validate against Alpaca (only tradeable tickers)
        try:
            validator = AlpacaAssetValidator()
            alpaca_assets = validator.fetch_all_assets()
        except Exception as e:
            logger.warning(
                f"[onboarder] Alpaca validation failed: {e}. "
                f"Skipping universe expansion."
            )
            return []

        candidates: list[str] = []
        for ticker in sorted(new_tickers):
            asset = alpaca_assets.get(ticker)
            if not asset or not asset.tradable:
                reason = "not found" if not asset else "not tradeable"
                logger.debug(
                    f"[onboarder] Skipping {ticker}: {reason} on Alpaca"
                )
                continue
            candidates.append(ticker)

        if not candidates:
            logger.info("[onboarder] No new Alpaca-tradeable tickers to add.")
            return []

        # Step 4: yfinance quoteType check BEFORE backfill
        # Only tickers confirmed as EQUITY will proceed. Non-equities
        # are blacklisted so they won't be checked again.
        verified = self._verify_equity_type(candidates)

        if not verified:
            logger.info("[onboarder] No verified equities to add.")
            return []

        # Step 5: Now add to universe + backfill (only verified equities)
        for ticker in verified:
            asset = alpaca_assets.get(ticker)
            self.manager.add_ticker(
                ticker=ticker,
                company_name=asset.name if asset else None,
                added_by=source,
                exchange=asset.exchange if asset else None,
            )

        # Commit universe additions so backfill can see them
        self.session.commit()

        logger.info(
            f"[onboarder] Added {len(verified)} tickers to universe: {verified}. "
            f"Starting auto-backfill..."
        )

        # Step 6: Auto-backfill (prices, TA, fundamentals, sector)
        self._backfill_prices(verified)
        self._backfill_indicators(verified)
        self._backfill_fundamentals(verified)
        self._enrich_sector(verified)

        logger.info(
            f"[onboarder] Onboarding complete for {len(verified)} tickers: {verified}"
        )
        return verified

    def _verify_equity_type(self, tickers: list[str]) -> list[str]:
        """Quick yfinance quoteType check before committing to backfill.

        Returns only tickers confirmed as EQUITY. Non-equities are
        added to the blacklist and never enter the universe.
        """
        import yfinance as yf

        from trading_signals.universe.blacklist import add_to_blacklist

        verified: list[str] = []
        blacklisted: list[str] = []

        for ticker in tickers:
            try:
                info = yf.Ticker(ticker).info
                qt = (info.get("quoteType", "UNKNOWN") if info else "UNKNOWN")

                if qt.upper() == "EQUITY":
                    verified.append(ticker)
                else:
                    add_to_blacklist(
                        self.session, ticker,
                        quote_type=qt,
                        source="onboarder/quoteType_check",
                    )
                    blacklisted.append(ticker)
                    logger.warning(
                        f"[onboarder] Pre-backfill check: {ticker} is "
                        f"{qt}, not EQUITY → blacklisted"
                    )
            except Exception as e:
                # If yfinance fails, let the ticker through (benefit of the doubt)
                verified.append(ticker)
                logger.debug(
                    f"[onboarder] quoteType check failed for {ticker}: {e}, "
                    f"allowing through"
                )

        if blacklisted:
            logger.info(
                f"[onboarder] Pre-backfill filter: {len(blacklisted)} "
                f"non-equities blocked, {len(verified)} equities verified"
            )

        return verified

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
        """Fetch sector/industry data for new tickers.

        Also acts as a secondary ETF filter: if yfinance reports
        quoteType != 'EQUITY', the ticker is deactivated (learned filter).
        """
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

            enriched = 0
            deactivated_etfs: list[str] = []

            with get_session() as session:
                from trading_signals.universe.blacklist import add_to_blacklist

                for record in results:
                    ticker = record["ticker"]
                    quote_type = record.get("quote_type", "")

                    # Learned ETF filter: add to blacklist + deactivate
                    if quote_type and quote_type.upper() != "EQUITY":
                        add_to_blacklist(
                            session, ticker,
                            quote_type=quote_type,
                            source="sector_enrichment",
                        )
                        session.execute(
                            update(Universe)
                            .where(Universe.ticker == ticker)
                            .values(is_active=False)
                        )
                        deactivated_etfs.append(ticker)
                        logger.warning(
                            f"[onboarder] Blacklisted + deactivated {ticker}: "
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
                    f"[onboarder] Blacklisted {len(deactivated_etfs)} non-equity "
                    f"tickers: {deactivated_etfs}"
                )

            logger.info(
                f"[onboarder] Sector enrichment: {enriched}/{len(tickers)} "
                f"tickers enriched"
            )

        except Exception as e:
            logger.error(f"[onboarder] Sector enrichment failed: {e}")
