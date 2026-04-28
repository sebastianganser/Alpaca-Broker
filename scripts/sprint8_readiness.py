"""Sprint 8 Readiness Check - robust version with try/except per table."""
from datetime import date
from trading_signals.db.session import get_session
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.technical_indicators import TechnicalIndicator
from trading_signals.db.models.universe import Universe
from sqlalchemy import func, text

today = date.today()

def safe_query(s, sql, label):
    try:
        return s.execute(text(sql)).first()
    except Exception as e:
        s.rollback()
        print(f"  [{label}] Query failed: {e.__class__.__name__}")
        return None

def safe_scalar(s, sql, label):
    try:
        return s.execute(text(sql)).scalar()
    except Exception as e:
        s.rollback()
        print(f"  [{label}] Query failed: {e.__class__.__name__}")
        return None

with get_session() as s:
    active = s.query(func.count()).select_from(Universe).filter(Universe.is_active.is_(True)).scalar()
    
    print(f"=== Sprint 8 Readiness Check ({today}) ===\n")
    print(f"Active Universe: {active} tickers\n")
    
    # Prices
    p_count = s.query(func.count()).select_from(PriceDaily).scalar()
    p_min = s.query(func.min(PriceDaily.trade_date)).scalar()
    p_max = s.query(func.max(PriceDaily.trade_date)).scalar()
    print(f"--- Prices ---")
    print(f"  Rows: {p_count:,}  |  {p_min} to {p_max}")
    
    # TA
    ta_count = s.query(func.count()).select_from(TechnicalIndicator).scalar()
    ta_max = s.query(func.max(TechnicalIndicator.trade_date)).scalar()
    rs_filled = s.query(func.count()).select_from(TechnicalIndicator).filter(
        TechnicalIndicator.relative_strength_spy.isnot(None)
    ).scalar()
    print(f"\n--- Technical Indicators ---")
    print(f"  Rows: {ta_count:,}  |  RS filled: {rs_filled:,} ({rs_filled/ta_count*100:.1f}%)")
    
    # ARK Holdings
    r = safe_query(s, "SELECT COUNT(*), COUNT(DISTINCT snapshot_date), MIN(snapshot_date), MAX(snapshot_date) FROM signals.ark_holdings", "ark_holdings")
    if r:
        print(f"\n--- ARK Holdings ---")
        print(f"  Rows: {r[0]:,}  |  {r[1]} snapshot days  |  {r[2]} to {r[3]}")
    
    # ARK Deltas
    r = safe_query(s, "SELECT COUNT(*), COUNT(DISTINCT computed_at::date), MIN(computed_at::date), MAX(computed_at::date) FROM signals.ark_deltas", "ark_deltas")
    if r:
        print(f"\n--- ARK Deltas ---")
        print(f"  Rows: {r[0]:,}  |  {r[1]} delta days  |  {r[2]} to {r[3]}")
        delta_days = r[1]
    else:
        delta_days = 0
    
    # Insider Trades
    r = safe_query(s, "SELECT COUNT(*), MIN(transaction_date), MAX(transaction_date) FROM signals.insider_trades", "insider_trades")
    if r:
        print(f"\n--- Insider Trades (Form 4) ---")
        print(f"  Rows: {r[0]:,}  |  {r[1]} to {r[2]}")
        insider_count = r[0]
    else:
        insider_count = 0
    
    # Insider Clusters
    r = safe_query(s, "SELECT COUNT(*), MIN(cluster_start), MAX(cluster_end) FROM signals.insider_clusters", "insider_clusters")
    if r:
        print(f"\n--- Insider Clusters ---")
        print(f"  Rows: {r[0]:,}  |  {r[1]} to {r[2]}")
    
    # 13F - try both possible table names
    r = safe_query(s, "SELECT COUNT(*), MIN(report_period), MAX(report_period) FROM signals.form13f_holdings", "13f")
    if r:
        print(f"\n--- Form 13F Holdings ---")
        print(f"  Rows: {r[0]:,}  |  {r[1]} to {r[2]}")
    
    # Politician Trades
    r = safe_query(s, "SELECT COUNT(*), MIN(disclosure_date), MAX(disclosure_date) FROM signals.politician_trades", "politicians")
    if r:
        print(f"\n--- Politician Trades ---")
        print(f"  Rows: {r[0]:,}  |  {r[1]} to {r[2]}")
        pol_count = r[0]
    else:
        pol_count = 0
    
    # Fundamentals
    r = safe_query(s, "SELECT COUNT(*), COUNT(DISTINCT ticker), COUNT(DISTINCT snapshot_date), MIN(snapshot_date), MAX(snapshot_date) FROM signals.fundamentals_snapshot", "fundamentals")
    if r:
        print(f"\n--- Fundamentals ---")
        print(f"  Rows: {r[0]:,}  |  {r[1]} tickers  |  {r[2]} weeks  |  {r[3]} to {r[4]}")
        fund_weeks = r[2]
    else:
        fund_weeks = 0
    
    # Analyst Ratings
    r = safe_query(s, "SELECT COUNT(*), MIN(rating_date), MAX(rating_date) FROM signals.analyst_ratings", "ratings")
    if r:
        print(f"\n--- Analyst Ratings ---")
        print(f"  Rows: {r[0]:,}  |  {r[1]} to {r[2]}")
    
    # Earnings
    r = safe_query(s, "SELECT COUNT(*), MIN(earnings_date), MAX(earnings_date) FROM signals.earnings_calendar", "earnings")
    if r:
        print(f"\n--- Earnings Calendar ---")
        print(f"  Rows: {r[0]:,}  |  {r[1]} to {r[2]}")
    
    # System uptime
    first_run = safe_scalar(s, "SELECT MIN(started_at)::date FROM signals.collection_log WHERE status = 'success'", "uptime")
    days_running = (today - first_run).days if first_run else 0
    
    print(f"\n{'='*60}")
    print(f"=== READINESS ASSESSMENT ===")
    print(f"{'='*60}")
    print(f"  System live seit:     {first_run} ({days_running} Tage)")
    print(f"  ARK-Delta-Tage:       {delta_days} von min. 20 (Conviction-Score)")
    print(f"  Fundamental-Wochen:   {fund_weeks} von min. 4 (Trend-Erkennung)")
    print(f"  Insider-Trades:       {insider_count:,} (gut, >500 reicht)")
    print(f"  Politiker-Trades:     {pol_count:,} (Nice-to-have)")
    print()
    
    # Recommendation
    if delta_days >= 20 and fund_weeks >= 4:
        print("  >>> BEREIT fuer Sprint 8! <<<")
    else:
        days_to_ark = max(0, 20 - delta_days)
        days_to_fund = max(0, (4 - fund_weeks) * 7)
        wait = max(days_to_ark, days_to_fund)
        target = date.fromordinal(today.toordinal() + wait)
        print(f"  >>> Noch ~{wait} Tage warten (Ziel: {target}) <<<")
        if days_to_ark > 0:
            print(f"      - ARK Deltas brauchen noch {days_to_ark} Tage")
        if days_to_fund > 0:
            print(f"      - Fundamentals brauchen noch {days_to_fund} Tage")
