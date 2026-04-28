"""Trigger a full TA backfill to recompute relative_strength_spy.

Run this ON THE SERVER (not remote) for best performance.
The UPSERT pattern will update existing records with the now-available
SPY relative strength values.

Usage (inside container):
    python -m scripts.recompute_spy_rs_fast
    
Or via docker exec:
    docker exec alpaca-broker python scripts/recompute_spy_rs_fast.py
"""
from trading_signals.db.session import get_session
from trading_signals.derived.technical_indicators import TechnicalIndicatorsComputer

print("=== Full TA Recompute (UPSERT mode) ===")
print("This will update relative_strength_spy for all existing records.")
print("Running on-server is recommended for performance.")
print()

with get_session() as session:
    computer = TechnicalIndicatorsComputer(session)
    
    # Verify SPY data is available
    computer._spy_df = computer._load_price_history("SPY")
    if computer._spy_df is None or len(computer._spy_df) == 0:
        print("ERROR: No SPY price data! Run backfill_spy.py first.")
        exit(1)
    
    print(f"SPY data: {len(computer._spy_df)} rows")
    print("Starting full backfill (this may take 15-30 minutes)...")
    
    total = computer.compute_all(backfill=True)
    print(f"\nDone! {total} records written/updated.")
