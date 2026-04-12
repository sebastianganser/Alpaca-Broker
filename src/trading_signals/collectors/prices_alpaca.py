"""Price Collector using Alpaca Market Data API.

Downloads daily OHLCV data for all active tickers in the universe.
Uses the multi-symbol bars endpoint for efficient batch downloads.

Features:
  - Multi-symbol batch endpoint (100 tickers per request)
  - Split + dividend adjusted prices (adjustment=all)
  - IEX feed (free tier)
  - Gap detection & repair before each run
  - Idempotent inserts (ON CONFLICT DO NOTHING)

Data endpoint:
  GET https://data.alpaca.markets/v2/stocks/bars
"""

from datetime import date, timedelta
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_signals.collectors.base import BaseCollector
from trading_signals.config import get_settings
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.universe import Universe
from trading_signals.utils.logging import get_logger
from trading_signals.utils.retry import retry

logger = get_logger(__name__)

# Alpaca Market Data base URL (different from paper trading API!)
DATA_BASE_URL = "https://data.alpaca.markets"

# Number of tickers per Alpaca batch request
BATCH_SIZE = 100

# Number of days to look back for daily price fetching
DEFAULT_LOOKBACK_DAYS = 10


class PriceCollectorAlpaca(BaseCollector):
    """Collect daily OHLCV prices from Alpaca Market Data API."""

    name = "prices_alpaca"

    def __init__(self, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> None:
        """Initialize the Alpaca price collector.

        Args:
            lookback_days: Number of calendar days to look back.
                           Default 10 provides buffer for weekends/holidays.
        """
        self.lookback_days = lookback_days
        settings = get_settings()
        self._headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }

    def fetch(self, session: Session) -> dict[str, list[dict]]:
        """Fetch OHLCV data for all active tickers in batches.

        Returns:
            Dict mapping ticker -> list of bar dicts.
        """
        stmt = (
            select(Universe.ticker)
            .where(Universe.is_active.is_(True))
            .order_by(Universe.ticker)
        )
        tickers = [row[0] for row in session.execute(stmt).all()]
        logger.info(
            f"[{self.name}] Fetching {len(tickers)} tickers "
            f"(lookback={self.lookback_days}d)"
        )

        end_date = date.today()
        start_date = end_date - timedelta(days=self.lookback_days)

        all_data: dict[str, list[dict]] = {}
        failed_count = 0

        for i in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[i : i + BATCH_SIZE]
            batch_num = (i // BATCH_SIZE) + 1
            total_batches = (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE

            logger.info(
                f"[{self.name}] Batch {batch_num}/{total_batches}: "
                f"{len(batch)} tickers"
            )

            try:
                bars = _fetch_bars_batch(
                    symbols=batch,
                    start=start_date.isoformat(),
                    end=end_date.isoformat(),
                    headers=self._headers,
                )
                for ticker, ticker_bars in bars.items():
                    if ticker_bars:
                        all_data[ticker] = ticker_bars
            except Exception as e:
                logger.error(f"[{self.name}] Batch {batch_num} failed: {e}")
                failed_count += len(batch)

        logger.info(
            f"[{self.name}] Fetched data for {len(all_data)} tickers. "
            f"Failed: {failed_count}"
        )
        return all_data

    def store(
        self, session: Session, data: dict[str, list[dict]]
    ) -> tuple[int, int]:
        """Store fetched price data in the database.

        Uses ON CONFLICT DO NOTHING for idempotent inserts.

        Returns:
            Tuple of (records_fetched, records_written).
        """
        records_fetched = 0
        records_written = 0

        for ticker, bars in data.items():
            for bar in bars:
                records_fetched += 1

                close_val = bar.get("c")
                if close_val is None:
                    continue

                trade_date = _parse_bar_timestamp(bar.get("t", ""))
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
                        adj_close=close_val,  # adjustment=all -> close IS adjusted
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
                    records_written += 1

        session.flush()
        logger.info(
            f"[{self.name}] Stored {records_written}/{records_fetched} records "
            f"({records_fetched - records_written} already existed)"
        )
        return records_fetched, records_written


@retry(max_attempts=3, base_delay=2.0)
def _fetch_bars_batch(
    symbols: list[str],
    start: str,
    end: str,
    headers: dict[str, str],
) -> dict[str, list[dict]]:
    """Fetch bars for multiple symbols from Alpaca.

    Args:
        symbols: List of ticker symbols (max 100).
        start: Start date (YYYY-MM-DD).
        end: End date (YYYY-MM-DD).
        headers: Alpaca auth headers.

    Returns:
        Dict mapping symbol -> list of bar dicts.
    """
    all_bars: dict[str, list[dict]] = {}
    next_page_token = None

    while True:
        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": start,
            "end": end,
            "limit": 10000,
            "adjustment": "all",
            "feed": "iex",
        }
        if next_page_token:
            params["page_token"] = next_page_token

        response = requests.get(
            f"{DATA_BASE_URL}/v2/stocks/bars",
            headers=headers,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()

        bars = payload.get("bars", {})
        for symbol, symbol_bars in bars.items():
            all_bars.setdefault(symbol, []).extend(symbol_bars)

        next_page_token = payload.get("next_page_token")
        if not next_page_token:
            break

    return all_bars


def _parse_bar_timestamp(timestamp: str) -> date | None:
    """Parse Alpaca bar timestamp (ISO format) to date."""
    if not timestamp:
        return None
    try:
        # Alpaca returns: "2026-04-07T04:00:00Z"
        return date.fromisoformat(timestamp[:10])
    except (ValueError, IndexError):
        return None
