"""Recompute historical feature snapshots including delisted tickers.

A1 Stufe 3: Survivorship Bias Prevention â€” Phase 3

Re-runs the FeaturePipeline for all historical dates where
feature_snapshots exist. This ensures that:
1. Delisted tickers (from A1 Stufe 2) get feature snapshots computed
2. Existing snapshots are updated with any pipeline changes
3. Cross-sectional features (percentiles) are recalculated with the
   correct Point-in-Time universe

WARNING: CPU-intensive! For ~100 dates Ã— 750+ tickers, expect ~30-60 min.
For a full rebuild of the retention window use
``scripts/repair/features_rebuild.py`` instead.

Review 2026-10: dates are clamped to ``data_start_date()`` and non-NYSE
sessions are skipped (the pipeline writes nothing for them). With
``--all-trading-days`` every NYSE session in [start, end] is recomputed,
not only dates that already have snapshots.

Usage:
    uv run python scripts/recompute_features.py [--start YYYY-MM-DD] \\
        [--end YYYY-MM-DD] [--all-trading-days] [--dry-run]
"""

import argparse
import sys
import time
from datetime import date
from pathlib import Path

from sqlalchemy import distinct, func, select

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trading_signals.db.models.features import FeatureSnapshot  # noqa: E402
from trading_signals.db.session import get_session  # noqa: E402
from trading_signals.derived.feature_pipeline import FeaturePipeline  # noqa: E402
from trading_signals.utils.logging import get_logger, setup_logging  # noqa: E402
from trading_signals.utils.market_calendar import (  # noqa: E402
    is_trading_day,
    last_completed_session,
    trading_days,
)
from trading_signals.utils.retention import data_start_date  # noqa: E402

setup_logging()
logger = get_logger(__name__)


def get_snapshot_dates(session, start: date | None, end: date | None) -> list[date]:
    """Get all distinct NYSE-session dates that have feature snapshots."""
    stmt = select(distinct(FeatureSnapshot.snapshot_date)).order_by(
        FeatureSnapshot.snapshot_date
    )
    if start:
        stmt = stmt.where(FeatureSnapshot.snapshot_date >= start)
    if end:
        stmt = stmt.where(FeatureSnapshot.snapshot_date <= end)

    return [row[0] for row in session.execute(stmt).all() if is_trading_day(row[0])]


def main():
    parser = argparse.ArgumentParser(
        description="Recompute historical feature snapshots"
    )
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--all-trading-days",
        action="store_true",
        help="Recompute every NYSE session in [start, end]",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show dates without computing"
    )
    args = parser.parse_args()

    floor = data_start_date()
    start_date = date.fromisoformat(args.start) if args.start else floor
    start_date = max(start_date, floor)
    end_date = date.fromisoformat(args.end) if args.end else None

    print("=" * 60)
    print("A1 Stufe 3: Recompute Historical Feature Snapshots")
    print("=" * 60)

    with get_session() as session:
        # Get dates to recompute
        if args.all_trading_days:
            dates = list(trading_days(start_date, end_date or last_completed_session()))
            print(f"\nNYSE sessions in window: {len(dates)}")
        else:
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
            print(f"  [{i}/{len(dates)}] {d}: {written} snapshots ({elapsed:.1f}s)")

            # Commit every 10 dates to avoid huge transaction
            if i % 10 == 0:
                session.commit()
                remaining = (len(dates) - i) * (time.time() - t_start) / i
                print(f"  ... committed. Estimated remaining: {remaining/60:.0f} min")

        # Final commit
        session.commit()

        elapsed_total = time.time() - t_start
        print("\n-- Summary --")
        print(f"  Dates recomputed: {len(dates)}")
        print(f"  Total snapshots:  {total_written}")
        print(f"  Total time:       {elapsed_total/60:.1f} min")


if __name__ == "__main__":
    main()
