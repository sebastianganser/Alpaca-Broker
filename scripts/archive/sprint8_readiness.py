"""Sprint 8 Readiness Check — with data depth and coverage validation.

Run inside the Docker container:
    docker exec -it alpaca-broker uv run python scripts/sprint8_readiness.py
"""
from datetime import date

from sqlalchemy import func, text

from trading_signals.config import DATA_START_DATE
from trading_signals.db.models.prices import PriceDaily
from trading_signals.db.models.technical_indicators import TechnicalIndicator
from trading_signals.db.models.universe import Universe
from trading_signals.db.session import get_session

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


def depth_check(min_date, expected_start, label):
    """Check if data reaches back far enough."""
    if min_date is None:
        return "❌ NO DATA"
    if min_date <= expected_start:
        return "✅ deep"
    days_short = (min_date - expected_start).days
    return f"⚠️  starts {min_date} ({days_short}d short)"


def coverage_check(s, table, ticker_col, date_col, label):
    """Check what % of active universe tickers have data."""
    try:
        r = s.execute(text(f"""
            SELECT
                COUNT(DISTINCT u.ticker) AS total,
                COUNT(DISTINCT t.{ticker_col}) AS with_data
            FROM signals.universe u
            LEFT JOIN signals.{table} t ON t.{ticker_col} = u.ticker
            WHERE u.is_active = true
        """)).first()
        if r and r[0] > 0:
            pct = 100 * r[1] / r[0]
            status = "✅" if pct >= 90 else "⚠️" if pct >= 70 else "❌"
            return f"{status} {r[1]}/{r[0]} ({pct:.0f}%)"
        return "❌ no universe"
    except Exception:
        s.rollback()
        return "⏭️ skip"


with get_session() as s:
    active = s.query(func.count()).select_from(Universe).filter(
        Universe.is_active.is_(True)
    ).scalar()

    print(f"\n{'='*70}")
    print(f"  SPRINT 8 READINESS CHECK ({today})")
    print(f"  DATA_START_DATE: {DATA_START_DATE}")
    print(f"{'='*70}\n")
    print(f"  Active Universe: {active} tickers\n")

    # ── Data Sources Overview ────────────────────────────────────
    print(f"{'─'*70}")
    print(f"  {'Source':<25} {'Rows':>10} {'From':>12} {'To':>12} "
          f"{'Depth':>20} {'Coverage':>18}")
    print(f"  {'─'*25} {'─'*10} {'─'*12} {'─'*12} {'─'*20} {'─'*18}")

    # Prices
    p_count = s.query(func.count()).select_from(PriceDaily).scalar()
    p_min = s.query(func.min(PriceDaily.trade_date)).scalar()
    p_max = s.query(func.max(PriceDaily.trade_date)).scalar()
    p_depth = depth_check(p_min, DATA_START_DATE, "prices")
    p_cov = coverage_check(s, "prices_daily", "ticker", "trade_date", "prices")
    print(f"  {'prices_daily':<25} {p_count:>10,} {str(p_min):>12} "
          f"{str(p_max):>12} {p_depth:>20} {p_cov:>18}")

    # TA
    ta_count = s.query(func.count()).select_from(TechnicalIndicator).scalar()
    ta_min = s.query(func.min(TechnicalIndicator.trade_date)).scalar()
    ta_max = s.query(func.max(TechnicalIndicator.trade_date)).scalar()
    ta_depth = depth_check(ta_min, DATA_START_DATE, "ta")
    ta_cov = coverage_check(
        s, "technical_indicators", "ticker", "trade_date", "ta"
    )
    print(f"  {'technical_indicators':<25} {ta_count:>10,} {str(ta_min):>12} "
          f"{str(ta_max):>12} {ta_depth:>20} {ta_cov:>18}")

    # ARK Holdings
    r = safe_query(
        s,
        "SELECT COUNT(*), MIN(snapshot_date), MAX(snapshot_date) "
        "FROM signals.ark_holdings",
        "ark",
    )
    if r:
        ark_depth = depth_check(r[1], date(2026, 4, 1), "ark")  # no backfill
        ark_cov = "ℹ️ accumulating"
        print(f"  {'ark_holdings':<25} {r[0]:>10,} {str(r[1]):>12} "
              f"{str(r[2]):>12} {ark_depth:>20} {ark_cov:>18}")

    # ARK Deltas
    r = safe_query(
        s,
        "SELECT COUNT(*), MIN(computed_at::date), MAX(computed_at::date) "
        "FROM signals.ark_deltas",
        "ark_deltas",
    )
    if r:
        print(f"  {'ark_deltas':<25} {r[0]:>10,} {str(r[1]):>12} "
              f"{str(r[2]):>12} {'ℹ️ derived':>20} {'—':>18}")
        delta_days = (r[2] - r[1]).days if r[1] and r[2] else 0
    else:
        delta_days = 0

    # Insider Trades
    r = safe_query(
        s,
        "SELECT COUNT(*), MIN(transaction_date), MAX(transaction_date) "
        "FROM signals.insider_trades",
        "insider_trades",
    )
    if r:
        ins_depth = depth_check(r[1], date(2023, 4, 1), "insider")
        ins_cov = coverage_check(
            s, "insider_trades", "ticker", "transaction_date", "insider"
        )
        print(f"  {'insider_trades':<25} {r[0]:>10,} {str(r[1]):>12} "
              f"{str(r[2]):>12} {ins_depth:>20} {ins_cov:>18}")
        insider_count = r[0]
    else:
        insider_count = 0

    # Insider Clusters
    r = safe_query(
        s,
        "SELECT COUNT(*), MIN(cluster_start), MAX(cluster_end) "
        "FROM signals.insider_clusters",
        "insider_clusters",
    )
    if r:
        print(f"  {'insider_clusters':<25} {r[0]:>10,} {str(r[1]):>12} "
              f"{str(r[2]):>12} {'ℹ️ derived':>20} {'—':>18}")

    # 13F
    r = safe_query(
        s,
        "SELECT COUNT(*), MIN(report_period), MAX(report_period) "
        "FROM signals.form13f_holdings",
        "13f",
    )
    if r:
        f13_depth = depth_check(r[1], date(2025, 6, 1), "13f")  # quarterly
        print(f"  {'form13f_holdings':<25} {r[0]:>10,} {str(r[1]):>12} "
              f"{str(r[2]):>12} {f13_depth:>20} {'ℹ️ top filers':>18}")

    # Politician Trades
    r = safe_query(
        s,
        "SELECT COUNT(*), MIN(disclosure_date), MAX(disclosure_date) "
        "FROM signals.politician_trades",
        "politicians",
    )
    if r:
        pol_depth = depth_check(r[1], date(2025, 4, 1), "politician")
        print(f"  {'politician_trades':<25} {r[0]:>10,} {str(r[1]):>12} "
              f"{str(r[2]):>12} {pol_depth:>20} {'ℹ️ Senate only':>18}")
        pol_count = r[0]
    else:
        pol_count = 0

    # Fundamentals
    r = safe_query(
        s,
        "SELECT COUNT(*), COUNT(DISTINCT ticker), "
        "COUNT(DISTINCT snapshot_date), "
        "MIN(snapshot_date), MAX(snapshot_date) "
        "FROM signals.fundamentals_snapshot",
        "fundamentals",
    )
    if r:
        fund_depth = depth_check(r[3], date(2026, 3, 1), "fund")  # no backfill
        fund_cov = coverage_check(
            s, "fundamentals_snapshot", "ticker", "snapshot_date", "fund"
        )
        print(f"  {'fundamentals_snapshot':<25} {r[0]:>10,} {str(r[3]):>12} "
              f"{str(r[4]):>12} {fund_depth:>20} {fund_cov:>18}")
        fund_weeks = r[2]
    else:
        fund_weeks = 0

    # Analyst Ratings
    r = safe_query(
        s,
        "SELECT COUNT(*), MIN(rating_date), MAX(rating_date) "
        "FROM signals.analyst_ratings",
        "ratings",
    )
    if r:
        rat_depth = depth_check(r[1], date(2026, 3, 1), "ratings")  # no backfill
        rat_cov = coverage_check(
            s, "analyst_ratings", "ticker", "rating_date", "ratings"
        )
        print(f"  {'analyst_ratings':<25} {r[0]:>10,} {str(r[1]):>12} "
              f"{str(r[2]):>12} {rat_depth:>20} {rat_cov:>18}")

    # Earnings
    r = safe_query(
        s,
        "SELECT COUNT(*), MIN(earnings_date), MAX(earnings_date) "
        "FROM signals.earnings_calendar",
        "earnings",
    )
    if r:
        earn_cov = coverage_check(
            s, "earnings_calendar", "ticker", "earnings_date", "earnings"
        )
        print(f"  {'earnings_calendar':<25} {r[0]:>10,} {str(r[1]):>12} "
              f"{str(r[2]):>12} {'✅ yfinance hist':>20} {earn_cov:>18}")

    # ── Readiness Assessment ─────────────────────────────────────
    first_run = safe_scalar(
        s,
        "SELECT MIN(started_at)::date FROM signals.collection_log "
        "WHERE status = 'success'",
        "uptime",
    )
    days_running = (today - first_run).days if first_run else 0

    print(f"\n{'='*70}")
    print(f"  READINESS ASSESSMENT")
    print(f"{'='*70}")
    print(f"  System live seit:     {first_run} ({days_running} Tage)")
    print(f"  ARK-Delta-Tage:       {delta_days} von min. 20 (Conviction-Score)")
    print(f"  Fundamental-Wochen:   {fund_weeks} von min. 4 (Trend-Erkennung)")
    print(f"  Insider-Trades:       {insider_count:,} (Ziel: >10k historisch)")
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

    print(f"\n{'='*70}\n")
