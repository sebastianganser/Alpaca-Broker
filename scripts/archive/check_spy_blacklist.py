"""Check if SPY is on the ticker blacklist."""
from trading_signals.db.session import get_session
from sqlalchemy import text

with get_session() as s:
    rows = s.execute(
        text("SELECT * FROM signals.ticker_blacklist WHERE ticker = 'SPY'")
    ).all()
    if rows:
        for r in rows:
            print(f"SPY blacklist entry: {dict(r._mapping)}")
    else:
        print("SPY is NOT on blacklist")
