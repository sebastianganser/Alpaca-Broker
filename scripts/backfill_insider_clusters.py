"""Backfill insider clusters from all historical trades.

Run inside the Docker container:
    docker exec -it alpaca-broker uv run python scripts/backfill_insider_clusters.py

This computes clusters across the full insider_trades history
(since 2023-01-01), not just the default 90-day window.
Uses UPSERT so it's safe to run multiple times.
"""

from datetime import date

from trading_signals.db.session import get_session
from trading_signals.derived.insider_clusters import InsiderClusterComputer


def main():
    since_date = date(2023, 1, 1)

    print(f"\n{'='*60}")
    print(f"  Insider Cluster Backfill")
    print(f"  Computing clusters from {since_date} to today")
    print(f"{'='*60}\n")

    with get_session() as session:
        computer = InsiderClusterComputer(session)
        written = computer.compute_new(since_date=since_date)
        session.commit()

    print(f"\n✅ Done: {written} clusters computed/updated")
    print(f"   (UPSERT — existing clusters were updated, new ones inserted)")


if __name__ == "__main__":
    main()
