"""Backfill historical price data from Alpaca Market Data API.

One-time script to load OHLCV data starting from 2021-01-01 for all
active tickers in the universe. This provides ~5.3 years of price
history needed for:
  - Technical indicators (SMA 200 needs 200 trading days)
  - ML model training (needs 500k+ samples)
  - Backtesting (Sprint 11)

Uses the same Alpaca multi-symbol batch endpoint as PriceCollectorAlpaca.
Idempotent: ON CONFLICT DO NOTHING ensures no duplicate rows.

Usage:
    uv run python scripts/backfill_prices.py
"""

import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

# Add src to path so we can import trading_signals
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trading_signals.config import get_settings
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.universe import Universe
from trading_signals.db.session import get_session
from trading_signals.utils.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)

# ── Configuration ──────────────────────────────────────────────────────

BACKFILL_START = date(2021, 1, 1)
DATA_BASE_URL = "https://data.alpaca.markets"
BATCH_SIZE = 100  # Max symbols per Alpaca request


def fetch_bars_batch(
    symbols: list[str],
    start: str,
    end: str,
    headers: dict[str, str],
) -> dict[str, list[dict]]:
    """Fetch bars for multiple symbols from Alpaca with pagination.

    Handles pagination via next_page_token to retrieve full history
    for multi-year date ranges.
    """
    all_bars: dict[str, list[dict]] = {}
    next_page_token = None
    page_count = 0

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
            timeout=60,  # Longer timeout for large date ranges
        )
        response.raise_for_status()
        payload = response.json()

        bars = payload.get("bars", {})
        for symbol, symbol_bars in bars.items():
            all_bars.setdefault(symbol, []).extend(symbol_bars)

        page_count += 1
        next_page_token = payload.get("next_page_token")
        if not next_page_token:
            break

    return all_bars


def parse_bar_timestamp(timestamp: str) -> date | None:
    """Parse Alpaca bar timestamp (ISO format) to date."""
    if not timestamp:
        return None
    try:
        return date.fromisoformat(timestamp[:10])
    except (ValueError, IndexError):
        return None


def main() -> None:
    """Run the price backfill."""
    logger.info("=" * 60)
    logger.info("Price Backfill – Starting")
    logger.info(f"  Date range: {BACKFILL_START} → {date.today()}")
    logger.info("=" * 60)

    settings = get_settings()
    if not settings.ALPACA_API_KEY:
        logger.error("ALPACA_API_KEY not configured. Aborting.")
        sys.exit(1)

    headers = {
        "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
    }

    with get_session() as session:
        # Get all active tickers
        stmt = (
            select(Universe.ticker)
            .where(Universe.is_active.is_(True))
            .order_by(Universe.ticker)
        )
        tickers = [row[0] for row in session.execute(stmt).all()]
        logger.info(f"Found {len(tickers)} active tickers in universe")

        # Check current row count
        count_before = session.execute(
            select(func.count()).select_from(PriceDaily)
        ).scalar_one()
        logger.info(f"Rows in prices_daily before backfill: {count_before:,}")

        end_date = date.today()
        start_str = BACKFILL_START.isoformat()
        end_str = end_date.isoformat()

        total_fetched = 0
        total_written = 0
        total_batches = (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE

        for i in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[i : i + BATCH_SIZE]
            batch_num = (i // BATCH_SIZE) + 1

            logger.info(
                f"Batch {batch_num}/{total_batches}: "
                f"{len(batch)} tickers ({batch[0]}..{batch[-1]})"
            )

            try:
                bars = fetch_bars_batch(
                    symbols=batch,
                    start=start_str,
                    end=end_str,
                    headers=headers,
                )

                batch_fetched = 0
                batch_written = 0

                for ticker, ticker_bars in bars.items():
                    for bar in ticker_bars:
                        batch_fetched += 1

                        close_val = bar.get("c")
                        if close_val is None:
                            continue

                        trade_date = parse_bar_timestamp(bar.get("t", ""))
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

                session.flush()
                total_fetched += batch_fetched
                total_written += batch_written

                logger.info(
                    f"  → {batch_fetched:,} bars fetched, "
                    f"{batch_written:,} new rows written "
                    f"({batch_fetched - batch_written:,} already existed)"
                )

            except Exception as e:
                logger.error(f"Batch {batch_num} failed: {e}")
                continue

            # Brief pause between batches to be a good API citizen
            if batch_num < total_batches:
                time.sleep(0.5)

        # Final count
        count_after = session.execute(
            select(func.count()).select_from(PriceDaily)
        ).scalar_one()

        logger.info("=" * 60)
        logger.info("Price Backfill – Complete")
        logger.info(f"  Total bars fetched: {total_fetched:,}")
        logger.info(f"  New rows written:   {total_written:,}")
        logger.info(f"  Already existed:    {total_fetched - total_written:,}")
        logger.info(f"  Rows before:        {count_before:,}")
        logger.info(f"  Rows after:         {count_after:,}")
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
