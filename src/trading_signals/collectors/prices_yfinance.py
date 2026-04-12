"""Price Collector using Yahoo Finance (yfinance).

Downloads daily OHLCV data for all active tickers in the universe.
Processes in batches of 50 to respect Yahoo's rate limits.

Features:
  - Batch download via yf.download()
  - Ticker mapping for special cases (BRK.B → BRK-B)
  - Gap detection & repair before each run
  - Idempotent inserts (ON CONFLICT DO NOTHING)
  - Partial success tracking (some tickers may fail)
"""

from datetime import date, timedelta
from typing import Any

import pandas as pd
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.collectors.gap_detector import (
    GapDetector,
    GapRepairResult,
    _safe_float,
    _safe_int,
)
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

# Tickers that need mapping between our universe and Yahoo Finance
TICKER_MAP_TO_YAHOO = {
    "BRK.B": "BRK-B",
}
TICKER_MAP_FROM_YAHOO = {v: k for k, v in TICKER_MAP_TO_YAHOO.items()}

# Number of tickers per yfinance batch request
BATCH_SIZE = 50


class PriceCollectorYFinance(BaseCollector):
    """Collect daily OHLCV prices from Yahoo Finance."""

    name = "prices_yfinance"

    def __init__(self, period: str = "10d") -> None:
        """Initialize the price collector.

        Args:
            period: yfinance period string for daily fetching.
                    Default "10d" provides a buffer for weekends/holidays.
        """
        self.period = period

    def check_and_repair_gaps(self, session: Session) -> GapRepairResult | None:
        """Check for data gaps and attempt to repair them."""
        # Get all active tickers that already have some data
        stmt = (
            select(Universe.ticker)
            .where(Universe.is_active.is_(True))
            .order_by(Universe.ticker)
        )
        all_tickers = [row[0] for row in session.execute(stmt).all()]

        # Only check tickers that already have data (new tickers aren't "gapped")
        tickers_with_data = []
        for ticker in all_tickers:
            exists = session.execute(
                select(PriceDaily.ticker)
                .where(PriceDaily.ticker == ticker)
                .limit(1)
            ).scalar_one_or_none()
            if exists:
                tickers_with_data.append(ticker)

        if not tickers_with_data:
            logger.info(f"[{self.name}] No existing data to gap-check")
            return None

        detector = GapDetector(session)
        gaps = detector.detect_gaps_bulk(tickers_with_data)

        if not gaps:
            logger.info(f"[{self.name}] No gaps detected")
            return GapRepairResult()

        total_gaps = sum(len(dates) for dates in gaps.values())
        logger.info(
            f"[{self.name}] Detected {total_gaps} gaps across "
            f"{len(gaps)} tickers. Repairing..."
        )

        return detector.repair_gaps(gaps, fetch_fn=self._fetch_for_gap_repair)

    def _fetch_for_gap_repair(
        self, tickers: list[str], start: date, end: date
    ) -> pd.DataFrame:
        """Fetch historical data for specific dates (used by GapDetector)."""
        yahoo_tickers = [TICKER_MAP_TO_YAHOO.get(t, t) for t in tickers]
        try:
            df = _download_with_retry(
                yahoo_tickers,
                start=start.isoformat(),
                end=end.isoformat(),
            )
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"Gap repair fetch failed: {e}")
            return pd.DataFrame()

    def fetch(self, session: Session) -> dict[str, pd.DataFrame]:
        """Fetch OHLCV data for all active tickers in batches.

        Returns:
            Dict mapping ticker → DataFrame of price data.
        """
        # Get all active tickers
        stmt = (
            select(Universe.ticker)
            .where(Universe.is_active.is_(True))
            .order_by(Universe.ticker)
        )
        universe_tickers = [row[0] for row in session.execute(stmt).all()]
        logger.info(
            f"[{self.name}] Fetching {len(universe_tickers)} tickers "
            f"(period={self.period})"
        )

        all_data: dict[str, pd.DataFrame] = {}
        failed_tickers: list[str] = []

        # Process in batches
        for i in range(0, len(universe_tickers), BATCH_SIZE):
            batch = universe_tickers[i : i + BATCH_SIZE]
            batch_num = (i // BATCH_SIZE) + 1
            total_batches = (len(universe_tickers) + BATCH_SIZE - 1) // BATCH_SIZE

            # Map our tickers to Yahoo tickers
            yahoo_batch = [TICKER_MAP_TO_YAHOO.get(t, t) for t in batch]

            logger.info(
                f"[{self.name}] Batch {batch_num}/{total_batches}: "
                f"{len(batch)} tickers"
            )

            try:
                df = _download_with_retry(
                    yahoo_batch,
                    period=self.period,
                )

                if df is None or df.empty:
                    logger.warning(f"[{self.name}] Batch {batch_num} returned empty")
                    failed_tickers.extend(batch)
                    continue

                # Parse multi-ticker result
                self._parse_batch_result(df, batch, yahoo_batch, all_data)

            except Exception as e:
                logger.error(f"[{self.name}] Batch {batch_num} failed: {e}")
                failed_tickers.extend(batch)

        logger.info(
            f"[{self.name}] Fetched data for {len(all_data)} tickers. "
            f"Failed: {len(failed_tickers)}"
        )

        return all_data

    def _parse_batch_result(
        self,
        df: pd.DataFrame,
        our_tickers: list[str],
        yahoo_tickers: list[str],
        all_data: dict[str, pd.DataFrame],
    ) -> None:
        """Parse yfinance batch download result into per-ticker DataFrames."""
        if len(our_tickers) == 1:
            # Single ticker: DataFrame has simple columns
            ticker = our_tickers[0]
            if not df.empty:
                all_data[ticker] = df
            return

        # Multi-ticker: columns are MultiIndex (Ticker, Field)
        # yfinance group_by="ticker" returns (Ticker, Price/Volume)
        for our_ticker, yahoo_ticker in zip(our_tickers, yahoo_tickers):
            try:
                if yahoo_ticker in df.columns.get_level_values(0):
                    ticker_df = df[yahoo_ticker].dropna(how="all")
                    if not ticker_df.empty:
                        all_data[our_ticker] = ticker_df
            except (KeyError, TypeError):
                # Ticker not in result – might have been delisted or invalid
                pass

    def store(
        self, session: Session, data: dict[str, pd.DataFrame]
    ) -> tuple[int, int]:
        """Store fetched price data in the database.

        Uses ON CONFLICT DO NOTHING for idempotent inserts.
        Raw data is sacred – never overwriting existing rows.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = 0
        records_written = 0

        for ticker, df in data.items():
            for trade_date_idx, row in df.iterrows():
                trade_date = (
                    trade_date_idx.date()
                    if hasattr(trade_date_idx, "date")
                    else trade_date_idx
                )
                records_fetched += 1

                close_val = _safe_float(row.get("Close"))
                if close_val is None:
                    continue  # Skip rows with no close price

                stmt = (
                    pg_insert(PriceDaily)
                    .values(
                        ticker=ticker,
                        trade_date=trade_date,
                        open=_safe_float(row.get("Open")),
                        high=_safe_float(row.get("High")),
                        low=_safe_float(row.get("Low")),
                        close=close_val,
                        adj_close=_safe_float(row.get("Adj Close")),
                        volume=_safe_int(row.get("Volume")),
                        source="yfinance",
                        is_extrapolated=False,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["ticker", "trade_date"]
                    )
                )
                result = session.execute(stmt)
                if result.rowcount > 0:
                    records_written += 1

        session.flush()
        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} records "
            f"({records_fetched - records_written} already existed)"
        )
        return records_fetched, records_written


@retry(max_attempts=3, base_delay=2.0)
def _download_with_retry(
    tickers: list[str],
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame | None:
    """Download data from yfinance with retry on transient failures."""
    kwargs: dict[str, Any] = {
        "tickers": tickers,
        "auto_adjust": False,
        "repair": True,
        "progress": False,
        "threads": True,
        "group_by": "ticker",
    }
    if period:
        kwargs["period"] = period
    if start:
        kwargs["start"] = start
    if end:
        kwargs["end"] = end

    df = yf.download(**kwargs)
    return df
