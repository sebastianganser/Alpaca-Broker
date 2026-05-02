"""Plausibility check for insider trades backfill.

Run after backfill_form4.py to verify data distribution:
    docker exec -it alpaca-broker uv run python scripts/verify_insider_backfill.py
"""

from trading_signals.db.session import get_session
from sqlalchemy import text


def main():
    with get_session() as s:
        # ── 1. Overall stats ────────────────────────────────────
        stats = s.execute(text("""
            SELECT
                COUNT(*)                          AS total_trades,
                COUNT(DISTINCT ticker)            AS tickers,
                COUNT(DISTINCT insider_name)      AS insiders,
                MIN(transaction_date)             AS earliest,
                MAX(transaction_date)             AS latest,
                MIN(filing_date)                  AS earliest_filing,
                MAX(filing_date)                  AS latest_filing
            FROM signals.insider_trades
        """)).first()

        print(f"\n{'='*70}")
        print(f"  INSIDER TRADES — BACKFILL VERIFICATION")
        print(f"{'='*70}\n")
        print(f"  Total trades:      {stats[0]:,}")
        print(f"  Distinct tickers:  {stats[1]:,}")
        print(f"  Distinct insiders: {stats[2]:,}")
        print(f"  Date range:        {stats[3]} → {stats[4]}")
        print(f"  Filing range:      {stats[5]} → {stats[6]}")

        # ── 2. Distribution by first letter ─────────────────────
        print(f"\n{'─'*70}")
        print(f"  DISTRIBUTION BY TICKER FIRST LETTER")
        print(f"{'─'*70}\n")

        letters = s.execute(text("""
            SELECT
                LEFT(ticker, 1)          AS letter,
                COUNT(DISTINCT ticker)   AS n_tickers,
                COUNT(*)                 AS n_trades
            FROM signals.insider_trades
            WHERE ticker IS NOT NULL
            GROUP BY 1
            ORDER BY 1
        """)).fetchall()

        print(f"  {'Letter':8} {'Tickers':>8} {'Trades':>10}")
        print(f"  {'─'*8} {'─'*8} {'─'*10}")
        for r in letters:
            bar = "█" * min(int(r[2] / 200), 40)
            print(f"  {r[0]:8} {r[1]:>8} {r[2]:>10,}  {bar}")

        # ── 3. Top 20 tickers by trade count ────────────────────
        print(f"\n{'─'*70}")
        print(f"  TOP 20 TICKERS BY TRADE COUNT")
        print(f"{'─'*70}\n")

        top = s.execute(text("""
            SELECT
                ticker,
                COUNT(*)                       AS n_trades,
                COUNT(DISTINCT insider_name)   AS n_insiders,
                MIN(transaction_date)          AS earliest,
                MAX(transaction_date)          AS latest
            FROM signals.insider_trades
            WHERE ticker IS NOT NULL
            GROUP BY ticker
            ORDER BY n_trades DESC
            LIMIT 20
        """)).fetchall()

        print(f"  {'Ticker':8} {'Trades':>7} {'Insiders':>9} {'Earliest':>12} {'Latest':>12}")
        print(f"  {'─'*8} {'─'*7} {'─'*9} {'─'*12} {'─'*12}")
        for r in top:
            print(f"  {r[0]:8} {r[1]:>7,} {r[2]:>9} {str(r[3]):>12} {str(r[4]):>12}")

        # ── 4. Bottom 20 (least trades) ─────────────────────────
        print(f"\n{'─'*70}")
        print(f"  BOTTOM 20 TICKERS BY TRADE COUNT")
        print(f"{'─'*70}\n")

        bottom = s.execute(text("""
            SELECT
                ticker,
                COUNT(*)                       AS n_trades,
                COUNT(DISTINCT insider_name)   AS n_insiders,
                MIN(transaction_date)          AS earliest,
                MAX(transaction_date)          AS latest
            FROM signals.insider_trades
            WHERE ticker IS NOT NULL
            GROUP BY ticker
            ORDER BY n_trades ASC
            LIMIT 20
        """)).fetchall()

        print(f"  {'Ticker':8} {'Trades':>7} {'Insiders':>9} {'Earliest':>12} {'Latest':>12}")
        print(f"  {'─'*8} {'─'*7} {'─'*9} {'─'*12} {'─'*12}")
        for r in bottom:
            print(f"  {r[0]:8} {r[1]:>7,} {r[2]:>9} {str(r[3]):>12} {str(r[4]):>12}")

        # ── 5. Monthly distribution ─────────────────────────────
        print(f"\n{'─'*70}")
        print(f"  MONTHLY DISTRIBUTION (filing_date)")
        print(f"{'─'*70}\n")

        months = s.execute(text("""
            SELECT
                TO_CHAR(filing_date, 'YYYY-MM') AS month,
                COUNT(*)                        AS n_trades,
                COUNT(DISTINCT ticker)          AS n_tickers
            FROM signals.insider_trades
            WHERE filing_date IS NOT NULL
            GROUP BY 1
            ORDER BY 1
        """)).fetchall()

        print(f"  {'Month':10} {'Trades':>8} {'Tickers':>8}")
        print(f"  {'─'*10} {'─'*8} {'─'*8}")
        for r in months:
            bar = "█" * min(int(r[1] / 100), 40)
            print(f"  {r[0]:10} {r[1]:>8,} {r[2]:>8}  {bar}")

        # ── 6. Universe coverage check ──────────────────────────
        print(f"\n{'─'*70}")
        print(f"  UNIVERSE COVERAGE")
        print(f"{'─'*70}\n")

        coverage = s.execute(text("""
            SELECT
                u.ticker,
                CASE WHEN COUNT(it.id) > 0 THEN 'YES' ELSE 'NO' END AS has_data,
                COUNT(it.id) AS n_trades
            FROM signals.universe u
            LEFT JOIN signals.insider_trades it ON it.ticker = u.ticker
            WHERE u.is_active = true
            GROUP BY u.ticker
            ORDER BY u.ticker
        """)).fetchall()

        with_data = sum(1 for r in coverage if r[1] == "YES")
        without_data = sum(1 for r in coverage if r[1] == "NO")

        print(f"  Active tickers:         {len(coverage)}")
        print(f"  With insider trades:    {with_data} "
              f"({100*with_data/len(coverage):.1f}%)")
        print(f"  Without insider trades: {without_data} "
              f"({100*without_data/len(coverage):.1f}%)")

        if without_data > 0 and without_data <= 50:
            no_data = [r[0] for r in coverage if r[1] == "NO"]
            print(f"\n  Tickers without data:")
            for i in range(0, len(no_data), 10):
                chunk = ", ".join(no_data[i:i+10])
                print(f"    {chunk}")

        # ── 7. Backfill depth analysis ───────────────────────────
        #    Classifies tickers by how far back their data reaches.
        #    Helps decide if skipped tickers need a deeper backfill.
        print(f"\n{'─'*70}")
        print(f"  BACKFILL DEPTH ANALYSIS")
        print(f"  (Do skipped tickers have deep history or only recent data?)")
        print(f"{'─'*70}\n")

        depth = s.execute(text("""
            SELECT
                u.ticker,
                COUNT(it.id)                AS n_trades,
                MIN(it.filing_date)         AS earliest_filing,
                MAX(it.filing_date)         AS latest_filing,
                MIN(it.transaction_date)    AS earliest_txn,
                MAX(it.transaction_date)    AS latest_txn
            FROM signals.universe u
            LEFT JOIN signals.insider_trades it ON it.ticker = u.ticker
            WHERE u.is_active = true
            GROUP BY u.ticker
            ORDER BY u.ticker
        """)).fetchall()

        # Classify: "deep" = earliest filing <= 2023-12-31 (has old data)
        #           "shallow" = earliest filing > 2023-12-31 (only recent)
        #           "none" = no data at all
        from datetime import date
        cutoff = date(2023, 12, 31)

        deep = []
        shallow = []
        none_list = []

        for r in depth:
            ticker, n_trades, earliest_f, latest_f, earliest_t, latest_t = r
            if n_trades == 0 or earliest_f is None:
                none_list.append(ticker)
            elif earliest_f <= cutoff:
                deep.append((ticker, n_trades, earliest_f, latest_f))
            else:
                shallow.append((ticker, n_trades, earliest_f, latest_f))

        total = len(depth)
        print(f"  Deep backfill (earliest filing ≤ {cutoff}):  "
              f"{len(deep):>4} tickers  ({100*len(deep)/total:.1f}%)")
        print(f"  Shallow / recent only (> {cutoff}):          "
              f"{len(shallow):>4} tickers  ({100*len(shallow)/total:.1f}%)")
        print(f"  No data at all:                              "
              f"{len(none_list):>4} tickers  ({100*len(none_list)/total:.1f}%)")

        if shallow:
            print(f"\n  ⚠️  SHALLOW TICKERS — may need deeper backfill:")
            print(f"  {'Ticker':8} {'Trades':>7} {'Earliest Filing':>16} "
                  f"{'Latest Filing':>14}")
            print(f"  {'─'*8} {'─'*7} {'─'*16} {'─'*14}")
            for t, n, ef, lf in sorted(shallow, key=lambda x: x[2]):
                print(f"  {t:8} {n:>7,} {str(ef):>16} {str(lf):>14}")

        if none_list:
            print(f"\n  ℹ️  Tickers with NO insider trades at all ({len(none_list)}):")
            for i in range(0, len(none_list), 10):
                chunk = ", ".join(none_list[i:i+10])
                print(f"    {chunk}")

        print(f"\n{'='*70}")
        print(f"  VERIFICATION COMPLETE")
        print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
