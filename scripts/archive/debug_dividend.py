"""Test plausibility validation with real yfinance data."""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')

import yfinance as yf
from trading_signals.collectors.yfinance_client import (
    FUNDAMENTALS_KEYS, _clean_numeric, _validate_fundamentals, _PLAUSIBILITY_RULES
)
from trading_signals.utils.logging import get_logger
import logging

# Enable WARNING level to see plausibility warnings
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

print("=== Testing plausibility validation ===\n")

# Test 1: Normal tickers
for sym in ["TSM", "AAPL", "MSFT", "JNJ"]:
    t = yf.Ticker(sym)
    info = t.info
    
    record = {"ticker": sym}
    for yf_key, db_key in FUNDAMENTALS_KEYS.items():
        record[db_key] = _clean_numeric(info.get(yf_key))
    
    # Apply dividend yield normalization
    if record.get("dividend_yield") is not None:
        record["dividend_yield"] = record["dividend_yield"] / 100
    
    # Validate
    _validate_fundamentals(record)
    
    dy = record.get("dividend_yield")
    pm = record.get("profit_margin")
    pe = record.get("pe_ratio")
    dy_display = f"{dy * 100:.2f}%" if dy else "None"
    pm_display = f"{pm * 100:.1f}%" if pm else "None"
    pe_display = f"{pe:.1f}" if pe else "None"
    print(f"{sym}: div_yield={dy_display}, profit_margin={pm_display}, PE={pe_display}")

print("\n=== Testing with FAKE bad data (simulating yfinance format change) ===\n")

# Test 2: Simulated bad data (what if yfinance changes format back to decimal?)
fake_record = {
    "ticker": "FAKE",
    "dividend_yield": 0.95,       # Would be 95% - clearly wrong!
    "profit_margin": 45.0,        # Would be 4500% - wrong format!
    "pe_ratio": -5.0,             # Negative PE - implausible
    "beta": 15.0,                 # Beta of 15 - implausible
    "market_cap": -1000,          # Negative market cap
    "current_ratio": 1.5,         # Normal - should pass
}
print(f"Before validation: {fake_record}")
_validate_fundamentals(fake_record)
print(f"After validation:  {fake_record}")
print("\n(Values that were out of range should be None now)")
