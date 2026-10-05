"""Remove SPY from blacklist and verify."""
from trading_signals.db.session import get_session
from sqlalchemy import text

with get_session() as s:
    s.execute(text("DELETE FROM signals.ticker_blacklist WHERE ticker = 'SPY'"))
    s.commit()
    print("SPY removed from blacklist")
