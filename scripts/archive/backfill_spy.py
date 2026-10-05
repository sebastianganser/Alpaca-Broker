"""Backfill SPY price data from Alpaca API and recompute relative_strength_spy."""
import sys
from datetime import date, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_signals.collectors.prices_alpaca import (
    _fetch_bars_batch,
    _parse_bar_timestamp,
    PriceCollectorAlpaca,
)
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.session import get_session

TICKER = "SPY"
LOOKBACK_DAYS = 5 * 365 + 180  # ~5.5 years to cover 2021-01-04

start_date = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
end_date = date.today().isoformat()

print(f"=== Backfilling {TICKER} from {start_date} to {end_date} ===")

# Use the Alpaca collector's headers (API key)
collector = PriceCollectorAlpaca(lookback_days=LOOKBACK_DAYS)

try:
    bars = _fetch_bars_batch(
        symbols=[TICKER],
        start=start_date,
        end=end_date,
        headers=collector._headers,
    )
except Exception as e:
    print(f"ERROR fetching bars: {e}")
    sys.exit(1)

spy_bars = bars.get(TICKER, [])
print(f"Fetched {len(spy_bars)} bars from Alpaca")

if not spy_bars:
    print("No data returned!")
    sys.exit(1)

written = 0
with get_session() as session:
    for bar in spy_bars:
        close_val = bar.get("c")
        if close_val is None:
            continue
        trade_date = _parse_bar_timestamp(bar.get("t", ""))
        if trade_date is None:
            continue

        stmt = (
            pg_insert(PriceDaily)
            .values(
                ticker=TICKER,
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
            .on_conflict_do_nothing(index_elements=["ticker", "trade_date"])
        )
        result = session.execute(stmt)
        if result.rowcount > 0:
            written += 1

print(f"Written {written} new SPY price records")

# Verify
spy_count = session.query(PriceDaily).filter(
    PriceDaily.ticker == TICKER
).count()
print(f"Total SPY records now: {spy_count}")
