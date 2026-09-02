"""Recompute historical feature snapshots including delisted tickers.

A1 Stufe 3: Survivorship Bias Prevention — Phase 3

Re-runs the FeaturePipeline for all historical dates where
feature_snapshots exist. This ensures that:
1. Delisted tickers (from A1 Stufe 2) get feature snapshots computed
2. Existing snapshots are updated with any pipeline changes
3. Cross-sectional features (percentiles) are recalculated with the
   correct Point-in-Time universe

WARNING: CPU-intensive! For ~100 dates × 750+ tickers, expect ~30-60 min.

Usage:
    uv run python scripts/recompute_features.py [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--dry-run]
"""

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import distinct, func, select

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trading_signals.config import DATA_START_DATE
from trading_signals.db.models.features import FeatureSnapshot
from trading_signals.db.session import get_session
from trading_signals.derived.feature_pipeline import FeaturePipeline
from trading_signals.utils.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


def get_snapshot_dates(session, start: date | None, end: date | None) -> list[date]:
    """Get all distinct dates that have feature snapshots."""
    stmt = select(distinct(FeatureSnapshot.snapshot_date)).order_by(
        FeatureSnapshot.snapshot_date
    )
    if start:
        stmt = stmt.where(FeatureSnapshot.snapshot_date >= start)
    if end:
        stmt = stmt.where(FeatureSnapshot.snapshot_date <= end)

    return [row[0] for row in session.execute(stmt).all()]


def main():
    parser = argparse.ArgumentParser(description="Recompute historical feature snapshots")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Show dates without computing")
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start) if args.start else None
    end_date = date.fromisoformat(args.end) if args.end else None

    print("=" * 60)
    print("A1 Stufe 3: Recompute Historical Feature Snapshots")
    print("=" * 60)

    with get_session() as session:
        # Get dates to recompute
        dates = get_snapshot_dates(session, start_date, end_date)
        print(f"\nDates with existing snapshots: {len(dates)}")

        if dates:
            print(f"  Range: {dates[0]} to {dates[-1]}")

        if args.dry_run:
            print("\n[DRY RUN] Would recompute features for these dates:")
            for d in dates:
                count = session.execute(
                    select(func.count())
                    .select_from(FeatureSnapshot)
                    .where(FeatureSnapshot.snapshot_date == d)
                ).scalar_one()
                print(f"  {d}: {count} snapshots")
            return

        if not dates:
            print("No dates to recompute.")
            return

        # Recompute features for each date
        pipeline = FeaturePipeline(session)
        total_written = 0
        t_start = time.time()

        for i, d in enumerate(dates, 1):
            t0 = time.time()
            written = pipeline.compute_daily(d)
            elapsed = time.time() - t0
            total_written += written
            print(
                f"  [{i}/{len(dates)}] {d}: {written} snapshots ({elapsed:.1f}s)"
            )

            # Commit every 10 dates to avoid huge transaction
            if i % 10 == 0:
                session.commit()
                remaining = (len(dates) - i) * (time.time() - t_start) / i
                print(f"  ... committed. Estimated remaining: {remaining/60:.0f} min")

        # Final commit
        session.commit()

        elapsed_total = time.time() - t_start
        print(f"\n-- Summary --")
        print(f"  Dates recomputed: {len(dates)}")
        print(f"  Total snapshots:  {total_written}")
        print(f"  Total time:       {elapsed_total/60:.1f} min")


if __name__ == "__main__":
    main()
