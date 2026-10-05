"""Check SPY price data coverage vs other tickers."""
from trading_signals.db.session import get_session
from trading_signals.db.models.prices import PriceDaily
from sqlalchemy import func

with get_session() as s:
    spy_min = s.query(func.min(PriceDaily.trade_date)).filter(
        PriceDaily.ticker == "SPY"
    ).scalar()
    spy_max = s.query(func.max(PriceDaily.trade_date)).filter(
        PriceDaily.ticker == "SPY"
    ).scalar()
    spy_count = s.query(func.count()).filter(
        PriceDaily.ticker == "SPY"
    ).scalar()

    aapl_min = s.query(func.min(PriceDaily.trade_date)).filter(
        PriceDaily.ticker == "AAPL"
    ).scalar()
    aapl_max = s.query(func.max(PriceDaily.trade_date)).filter(
        PriceDaily.ticker == "AAPL"
    ).scalar()
    aapl_count = s.query(func.count()).filter(
        PriceDaily.ticker == "AAPL"
    ).scalar()

    print("=== SPY Price Coverage ===")
    print(f"SPY:  {spy_count:>6} rows  |  {spy_min} to {spy_max}")
    print(f"AAPL: {aapl_count:>6} rows  |  {aapl_min} to {aapl_max}")
    print()

    if spy_count == 0:
        print("WARNING: SPY has NO price data at all!")
        print(f"  Need full backfill from {aapl_min} to {aapl_max}")
    elif spy_max and aapl_max and spy_max < aapl_max:
        gap_days = (aapl_max - spy_max).days
        print(f"WARNING: SPY gap: {spy_max} to {aapl_max} ({gap_days} calendar days)")
    else:
        print("OK: SPY coverage looks complete")
