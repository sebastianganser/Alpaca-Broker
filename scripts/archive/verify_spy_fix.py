"""Verify SPY data and relative_strength_spy coverage after backfill."""
from trading_signals.db.session import get_session
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.technical_indicators import TechnicalIndicator
from trading_signals.db.models.universe import Universe
from sqlalchemy import func

with get_session() as s:
    # 1. SPY universe status
    spy = s.query(Universe).filter(Universe.ticker == "SPY").first()
    print("=== SPY Universe Status ===")
    print(f"  is_active: {spy.is_active if spy else 'NOT FOUND'}")
    print(f"  added_by:  {spy.added_by if spy else '-'}")

    # 2. SPY price coverage
    spy_count = s.query(func.count()).select_from(PriceDaily).filter(
        PriceDaily.ticker == "SPY"
    ).scalar()
    spy_min = s.query(func.min(PriceDaily.trade_date)).filter(
        PriceDaily.ticker == "SPY"
    ).scalar()
    spy_max = s.query(func.max(PriceDaily.trade_date)).filter(
        PriceDaily.ticker == "SPY"
    ).scalar()
    print(f"\n=== SPY Prices ===")
    print(f"  Rows: {spy_count}")
    print(f"  Range: {spy_min} to {spy_max}")

    # 3. relative_strength_spy coverage
    total_ta = s.query(func.count()).select_from(TechnicalIndicator).scalar()
    null_rs = s.query(func.count()).select_from(TechnicalIndicator).filter(
        TechnicalIndicator.relative_strength_spy.is_(None)
    ).scalar()
    filled_rs = total_ta - null_rs
    pct = (filled_rs / total_ta * 100) if total_ta else 0
    print(f"\n=== relative_strength_spy ===")
    print(f"  Total TA records:  {total_ta}")
    print(f"  With RS value:     {filled_rs} ({pct:.1f}%)")
    print(f"  Still NULL:        {null_rs} ({100-pct:.1f}%)")

    # 4. Verdict
    print(f"\n=== Verdict ===")
    if null_rs == 0:
        print("  ALL GOOD - relative_strength_spy is fully populated!")
    elif null_rs < total_ta * 0.05:
        print(f"  MOSTLY OK - {null_rs} NULLs remaining (likely tickers with <20 days of data)")
    else:
        print(f"  WARNING - {null_rs} NULLs remaining, needs investigation")
