"""Verify dividend yield normalization works correctly."""

import yfinance as yf

# Simulate what the collector does
from trading_signals.collectors.yfinance_client import FUNDAMENTALS_KEYS, _clean_numeric

for sym in ["TSM", "AAPL", "MSFT", "JNJ"]:
    t = yf.Ticker(sym)
    info = t.info
    
    raw_dy = info.get("dividendYield")
    cleaned = _clean_numeric(raw_dy)
    normalized = cleaned / 100 if cleaned is not None else None
    display = f"{normalized * 100:.2f}%" if normalized else "—"
    
    print(f"{sym}: raw={raw_dy}, cleaned={cleaned}, normalized={normalized}, display={display}")
