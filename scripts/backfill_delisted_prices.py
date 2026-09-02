"""Identify and backfill price data for delisted/removed index tickers.

A1 Stufe 2: Survivorship Bias Prevention — Phase 2

Identifies tickers that were once members of S&P 500 or Nasdaq 100
but have since been removed (valid_to IS NOT NULL in index_membership).
For each such ticker:
  1. Creates a universe entry (is_active=False) if missing
  2. Downloads historical price data from Alpaca (2021-01-01 to valid_to)
  3. Reports results

This prevents survivorship bias in ML training by ensuring delisted
tickers have price history for feature computation.

Usage:
    uv run python scripts/backfill_delisted_prices.py [--dry-run]
"""

import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

# Add src to path so we can import trading_signals
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trading_signals.config import DATA_START_DATE, get_settings
from trading_signals.db.models.index_membership import IndexMembership
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.universe import Universe
from trading_signals.db.session import get_session
from trading_signals.utils.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)

# ── Configuration ──────────────────────────────────────────────────────

DATA_BASE_URL = "https://data.alpaca.markets"
BATCH_SIZE = 100  # Max symbols per Alpaca request


def identify_delisted_tickers(session) -> list[dict]:
    """Find tickers that were removed from an index (valid_to IS NOT NULL).

    Returns list of dicts with ticker, index_name, valid_from, valid_to.
    Only includes tickers NOT currently in any active index membership.
    """
    # All tickers with at least one closed interval
    closed = (
        select(
            IndexMembership.ticker,
            IndexMembership.index_name,
            IndexMembership.valid_from,
            IndexMembership.valid_to,
            IndexMembership.reason,
        )
        .where(IndexMembership.valid_to.isnot(None))
    )
    closed_rows = session.execute(closed).all()

    if not closed_rows:
        logger.info("No closed index memberships found.")
        return []

    # Find which of these tickers still have an active membership
    active_tickers = set(
        r[0]
        for r in session.execute(
            select(IndexMembership.ticker).where(
                IndexMembership.valid_to.is_(None)
            )
        ).all()
    )

    # Only keep truly delisted ones (not in any current index)
    delisted = []
    seen = set()
    for row in closed_rows:
        ticker = row[0]
        if ticker not in active_tickers and ticker not in seen:
            seen.add(ticker)
            delisted.append({
                "ticker": ticker,
                "index_name": row[1],
                "valid_from": row[2],
                "valid_to": row[3],
                "reason": row[4],
            })

    return sorted(delisted, key=lambda x: x["ticker"])


def ensure_universe_entries(session, delisted: list[dict]) -> int:
    """Create universe entries for delisted tickers (is_active=False).

    Returns number of new entries created.
    """
    created = 0
    for item in delisted:
        ticker = item["ticker"]
        existing = session.execute(
            select(Universe).where(Universe.ticker == ticker)
        ).scalar_one_or_none()

        if existing is None:
            entry = Universe(
                ticker=ticker,
                company_name=None,
                added_date=item["valid_from"],
                added_by="delisted_backfill",
                is_active=False,
            )
            session.add(entry)
            created += 1
            logger.info(f"  Created universe entry: {ticker} (is_active=False)")
        elif existing.is_active:
            # Ticker is still active in universe but removed from index
            # This can happen (e.g. added via ARK but dropped from S&P)
            logger.info(f"  {ticker}: already in universe (is_active=True), skipping")

    session.flush()
    return created


def fetch_bars_batch(
    symbols: list[str],
    start: str,
    end: str,
    headers: dict[str, str],
) -> dict[str, list[dict]]:
    """Fetch bars for multiple symbols from Alpaca with pagination."""
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


def backfill_prices(session, tickers: list[str], headers: dict) -> tuple[int, int]:
    """Download and store historical prices for given tickers.

    Returns (total_fetched, total_written).
    """
    end_date = date.today()
    start_date = DATA_START_DATE

    total_fetched = 0
    total_written = 0

    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i: i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        total_batches = (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(
            f"  Batch {batch_num}/{total_batches}: {len(batch)} tickers"
        )

        try:
            bars = fetch_bars_batch(
                symbols=batch,
                start=start_date.isoformat(),
                end=end_date.isoformat(),
                headers=headers,
            )

            for ticker, ticker_bars in bars.items():
                for bar in ticker_bars:
                    total_fetched += 1
                    close_val = bar.get("c")
                    if close_val is None:
                        continue

                    t_str = bar.get("t", "")
                    if not t_str:
                        continue
                    try:
                        trade_date = date.fromisoformat(t_str[:10])
                    except (ValueError, IndexError):
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
                            source="alpaca_delisted_backfill",
                            is_extrapolated=False,
                        )
                        .on_conflict_do_nothing(
                            index_elements=["ticker", "trade_date"]
                        )
                    )
                    result = session.execute(stmt)
                    if result.rowcount > 0:
                        total_written += 1

            session.flush()
            logger.info(
                f"  Batch {batch_num}: fetched {sum(len(v) for v in bars.values())} bars "
                f"for {len(bars)} tickers"
            )

        except Exception as e:
            logger.error(f"  Batch {batch_num} failed: {e}")

        # Small delay between batches
        if i + BATCH_SIZE < len(tickers):
            time.sleep(1)

    return total_fetched, total_written


def main():
    dry_run = "--dry-run" in sys.argv

    print("=" * 60)
    print("A1 Stufe 2: Delisted Ticker Backfill")
    print("=" * 60)

    settings = get_settings()
    headers = {
        "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
    }

    with get_session() as session:
        # Step 1: Identify delisted tickers
        print("\n-- Step 1: Identifying delisted tickers --")
        delisted = identify_delisted_tickers(session)

        if not delisted:
            print("No delisted tickers found in index_membership.")
            return

        print(f"\nFound {len(delisted)} delisted tickers:")
        for item in delisted:
            print(
                f"  {item['ticker']:6s} | {item['index_name']:10s} | "
                f"{item['valid_from']} -> {item['valid_to']} | {item.get('reason', '')}"
            )

        # Check which ones already have price data
        tickers_need_backfill = []
        for item in delisted:
            ticker = item["ticker"]
            count = session.execute(
                select(func.count())
                .select_from(PriceDaily)
                .where(PriceDaily.ticker == ticker)
            ).scalar_one()
            if count == 0:
                tickers_need_backfill.append(ticker)
                print(f"  {ticker}: NO price data -> needs backfill")
            else:
                print(f"  {ticker}: {count} rows -> already has data")

        if dry_run:
            print(f"\n[DRY RUN] Would backfill {len(tickers_need_backfill)} tickers")
            return

        # Step 2: Create universe entries
        print(f"\n-- Step 2: Creating universe entries --")
        created = ensure_universe_entries(session, delisted)
        print(f"  Created {created} new universe entries")

        # Step 3: Backfill prices
        if tickers_need_backfill:
            print(f"\n-- Step 3: Backfilling prices for {len(tickers_need_backfill)} tickers --")
            fetched, written = backfill_prices(
                session, tickers_need_backfill, headers
            )
            print(f"\n  Total: {fetched} bars fetched, {written} written")
        else:
            print("\n-- Step 3: All delisted tickers already have price data --")

        # Step 4: Summary
        print(f"\n-- Summary --")
        print(f"  Delisted tickers identified: {len(delisted)}")
        print(f"  Universe entries created:     {created}")
        print(f"  Tickers backfilled:           {len(tickers_need_backfill)}")


if __name__ == "__main__":
    main()
