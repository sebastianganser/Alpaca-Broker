"""Quick diagnostic for insider trades data distribution."""
from trading_signals.db.session import get_session
from sqlalchemy import text

with get_session() as s:
    rows = s.execute(text("""
        SELECT
            date_trunc('month', transaction_date) AS month,
            COUNT(*) AS trades,
            COUNT(DISTINCT ticker) AS tickers,
            COUNT(DISTINCT insider_name) AS insiders,
            COUNT(*) FILTER (WHERE transaction_type = 'P' AND is_derivative = false) AS buys
        FROM signals.insider_trades
        WHERE transaction_date IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)).fetchall()

    print("Month          | Trades | Tickers | Insiders | Non-Deriv Buys")
    print("-" * 70)
    for r in rows:
        print(f"{str(r[0])[:7]:15}| {r[1]:6} | {r[2]:7} | {r[3]:8} | {r[4]:14}")

    total = s.execute(text(
        "SELECT COUNT(*), MIN(transaction_date), MAX(transaction_date) FROM signals.insider_trades"
    )).first()
    print(f"\nTotal: {total[0]} trades, range: {total[1]} to {total[2]}")

    # Check for potential clusters in historical data
    potential = s.execute(text("""
        SELECT ticker,
               transaction_date,
               COUNT(DISTINCT insider_name) AS n_insiders,
               COUNT(*) AS n_buys
        FROM signals.insider_trades
        WHERE transaction_type = 'P'
          AND is_derivative = false
          AND transaction_date IS NOT NULL
        GROUP BY ticker, transaction_date
        HAVING COUNT(DISTINCT insider_name) >= 2
        ORDER BY transaction_date
    """)).fetchall()
    print(f"\nDates with >=2 insiders buying same ticker same day: {len(potential)}")
    for p in potential:
        print(f"  {p[0]:8} {p[1]}  insiders={p[2]} buys={p[3]}")

    # Existing clusters
    clusters = s.execute(text(
        "SELECT ticker, cluster_start, cluster_end, n_insiders, n_buys, cluster_score "
        "FROM signals.insider_clusters ORDER BY cluster_start"
    )).fetchall()
    print(f"\nExisting clusters ({len(clusters)}):")
    for c in clusters:
        print(f"  {c[0]:8} {c[1]} - {c[2]}  insiders={c[3]} buys={c[4]} score={c[5]}")
