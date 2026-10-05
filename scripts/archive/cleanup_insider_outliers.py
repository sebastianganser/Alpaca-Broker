"""Cleanup insider trade date outliers.

Removes trades with transaction_date outside the valid range:
  - Before DATA_START_DATE (2021-01-01): typos, ancient amendments
  - After today: vesting schedules, data entry errors

Run inside the Docker container:
    docker exec -it alpaca-broker uv run python scripts/cleanup_insider_outliers.py
"""

from datetime import date

from sqlalchemy import text

from trading_signals.config import DATA_START_DATE
from trading_signals.db.session import get_session


def main():
    today = date.today()

    print(f"\n{'='*60}")
    print(f"  INSIDER TRADES — OUTLIER CLEANUP")
    print(f"  Valid range: {DATA_START_DATE} → {today}")
    print(f"{'='*60}\n")

    with get_session() as session:
        # ── Count outliers before deletion ───────────────────────
        counts = session.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE transaction_date < :start)
                    AS before_start,
                COUNT(*) FILTER (WHERE transaction_date > :today)
                    AS after_today,
                COUNT(*) FILTER (
                    WHERE transaction_date >= :start
                      AND transaction_date <= :today
                ) AS valid
            FROM signals.insider_trades
        """), {"start": DATA_START_DATE, "today": today}).first()

        print(f"  Total trades:          {counts[0]:,}")
        print(f"  Before {DATA_START_DATE}:    {counts[1]:,}")
        print(f"  After {today}:       {counts[2]:,}")
        print(f"  Valid (to keep):       {counts[3]:,}")
        print()

        if counts[1] == 0 and counts[2] == 0:
            print("  ✅ No outliers found. Nothing to do.")
            return

        # ── Show samples of what will be deleted ─────────────────
        if counts[1] > 0:
            print(f"  Trades BEFORE {DATA_START_DATE} (samples):")
            old = session.execute(text("""
                SELECT ticker, insider_name, transaction_date,
                       filing_date, transaction_type, is_derivative
                FROM signals.insider_trades
                WHERE transaction_date < :start
                ORDER BY transaction_date ASC
                LIMIT 10
            """), {"start": DATA_START_DATE}).fetchall()
            for r in old:
                deriv = "deriv" if r[5] else "stock"
                print(f"    {r[0] or '???':8} {r[2]}  "
                      f"filed={r[3]}  type={r[4]}  {deriv}  {r[1]}")

        if counts[2] > 0:
            print(f"\n  Trades AFTER {today} (samples):")
            future = session.execute(text("""
                SELECT ticker, insider_name, transaction_date,
                       filing_date, transaction_type, is_derivative
                FROM signals.insider_trades
                WHERE transaction_date > :today
                ORDER BY transaction_date DESC
                LIMIT 10
            """), {"today": today}).fetchall()
            for r in future:
                deriv = "deriv" if r[5] else "stock"
                print(f"    {r[0] or '???':8} {r[2]}  "
                      f"filed={r[3]}  type={r[4]}  {deriv}  {r[1]}")

        # ── Delete outliers ──────────────────────────────────────
        print(f"\n  Deleting outliers...")

        deleted_old = session.execute(text("""
            DELETE FROM signals.insider_trades
            WHERE transaction_date < :start
        """), {"start": DATA_START_DATE}).rowcount

        deleted_future = session.execute(text("""
            DELETE FROM signals.insider_trades
            WHERE transaction_date > :today
        """), {"today": today}).rowcount

        session.commit()

        total_deleted = deleted_old + deleted_future

        print(f"\n  ✅ Deleted {total_deleted:,} outlier trades:")
        print(f"     Before {DATA_START_DATE}: {deleted_old:,}")
        print(f"     After {today}:       {deleted_future:,}")

        # ── Verify ───────────────────────────────────────────────
        new_range = session.execute(text("""
            SELECT MIN(transaction_date), MAX(transaction_date), COUNT(*)
            FROM signals.insider_trades
        """)).first()

        print(f"\n  Remaining: {new_range[2]:,} trades")
        print(f"  Date range: {new_range[0]} → {new_range[1]}")
        print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
