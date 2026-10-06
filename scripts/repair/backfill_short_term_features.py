"""Backfill the short-term price features ``st_*`` (concept 2026-10-06 §7.1).

Why: step A2 adds nine price-based features (migration 034). A full
feature rebuild would take 7-12 hours; these features depend only on
``prices_daily`` and past earnings dates, so they are computed vectorised
per ticker and written into the existing snapshots (only the ``st_*``
columns and ``feature_version`` change, all other features stay as they
are).

Usage (inside the container):
    .venv/bin/python scripts/repair/backfill_short_term_features.py           # dry run
    .venv/bin/python scripts/repair/backfill_short_term_features.py --apply
"""

from __future__ import annotations

import argparse
import sys
import time

from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("repair.short_term_backfill")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="Write to the DB (default: dry run)")
    p.add_argument("--batch", type=int, default=50, help="Tickers per commit")
    return p.parse_args(argv)


def report(session) -> None:
    from sqlalchemy import func, select

    from trading_signals.db.models.features import FeatureSnapshot
    from trading_signals.derived.feature_pipeline import FEATURE_VERSION

    total = session.execute(select(func.count()).select_from(FeatureSnapshot)).scalar()
    filled = session.execute(
        select(func.count())
        .select_from(FeatureSnapshot)
        .where(FeatureSnapshot.st_return_1d.isnot(None))
    ).scalar()
    current = session.execute(
        select(func.count())
        .select_from(FeatureSnapshot)
        .where(FeatureSnapshot.feature_version == FEATURE_VERSION)
    ).scalar()
    logger.info(
        f"feature_snapshots={total}, st_return_1d filled={filled}, "
        f"version {FEATURE_VERSION}={current}"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")

    from trading_signals.db.session import get_session
    from trading_signals.derived.feature_pipeline import FEATURE_VERSION
    from trading_signals.derived.short_term_features import ShortTermBackfill

    with get_session() as session:
        report(session)
        comp = ShortTermBackfill(session)
        tickers = comp.all_tickers()
        logger.info(f"{len(tickers)} tickers with snapshots")
        if not args.apply:
            logger.info("DRY RUN – nothing written. Re-run with --apply.")
            return 0
        t0 = time.time()
        rows = 0
        batch = max(1, args.batch)
        for i in range(0, len(tickers), batch):
            rows += comp.recompute_tickers(tickers[i : i + batch], FEATURE_VERSION)
            session.commit()
            logger.info(
                f"short-term {min(i + batch, len(tickers))}/{len(tickers)} tickers, "
                f"{rows} rows, {time.time() - t0:.0f}s"
            )
        report(session)
    logger.info("Short-term feature backfill finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
