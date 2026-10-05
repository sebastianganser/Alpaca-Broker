"""Rebuild the feature store with FEATURE_VERSION 2026.10-1 (review repair).

Why: the 2026-10 review found look-ahead bias (insider clusters, politician
transaction dates, news after the close, macro values, earnings dates),
stale values surviving recomputes, snapshots on non-trading days and a
target definition that was not tradable. All snapshots inside the
retention window must be recomputed with the fixed pipeline.

Steps (each can be skipped):
  1. counts / plan (always; this is all a DRY RUN does)
  2. technical_indicators: full backfill from data_start_date() on real
     (non-extrapolated) prices, committed every ``--batch`` tickers
  3. insider_clusters: delete + rebuild (with known_date)
  4. feature_snapshots: for every NYSE session in [start, end]:
     DELETE the day's rows, FeaturePipeline.compute_daily(d), commit every
     ``--commit-every`` days → resumable with ``--start``
  5. targets: TargetBackfillComputer.recompute_tickers in ticker chunks
     (new definition close(d+h)/open(d+1) - 1), committed per chunk

Runtime: roughly 20-35 s per session for ~750 tickers → a full 5-year
rebuild (~1250 sessions) takes about 7-12 hours. Run it inside the
container with ``nohup`` / tmux; if interrupted, re-run with
``--skip-ta --skip-clusters --start <last logged date>``.

Usage:
    uv run python scripts/repair/features_rebuild.py                  # dry run
    uv run python scripts/repair/features_rebuild.py --apply
    uv run python scripts/repair/features_rebuild.py --apply \\
        --skip-ta --skip-clusters --start 2024-03-01
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("repair.features_rebuild")

#: Rough cost per session (seconds) for the runtime estimate.
SECONDS_PER_SESSION = (20, 35)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--apply", action="store_true", help="Write to the DB (default: dry run)"
    )
    p.add_argument("--start", type=date.fromisoformat, default=None,
                   help="First snapshot date (default: data_start_date())")
    p.add_argument("--end", type=date.fromisoformat, default=None,
                   help="Last snapshot date (default: last completed session)")
    p.add_argument("--skip-ta", action="store_true", help="Skip step 2")
    p.add_argument("--skip-clusters", action="store_true", help="Skip step 3")
    p.add_argument("--skip-features", action="store_true", help="Skip step 4")
    p.add_argument("--skip-targets", action="store_true", help="Skip step 5")
    p.add_argument("--batch", type=int, default=50,
                   help="Tickers per commit for TA / targets")
    p.add_argument("--commit-every", type=int, default=5,
                   help="Sessions per commit for feature snapshots")
    return p.parse_args(argv)


def plan_sessions(start: date, end: date) -> list[date]:
    """NYSE sessions in [start, end] (pure apart from the calendar)."""
    from trading_signals.utils.market_calendar import trading_days

    if end < start:
        return []
    return list(trading_days(start, end))


def resolve_window(args: argparse.Namespace) -> tuple[date, date]:
    from trading_signals.utils.market_calendar import last_completed_session
    from trading_signals.utils.retention import data_start_date

    floor = data_start_date()
    start = max(args.start or floor, floor)
    end = args.end or last_completed_session()
    return start, end


def report_counts(session) -> None:
    from sqlalchemy import func, select

    from trading_signals.db.models.features import FeatureSnapshot
    from trading_signals.db.models.insider import InsiderCluster
    from trading_signals.db.models.technical_indicators import TechnicalIndicator
    from trading_signals.derived.feature_pipeline import FEATURE_VERSION

    total = session.execute(select(func.count()).select_from(FeatureSnapshot)).scalar()
    current = session.execute(
        select(func.count())
        .select_from(FeatureSnapshot)
        .where(FeatureSnapshot.feature_version == FEATURE_VERSION)
    ).scalar()
    ta = session.execute(select(func.count()).select_from(TechnicalIndicator)).scalar()
    cl = session.execute(select(func.count()).select_from(InsiderCluster)).scalar()
    logger.info(
        f"feature_snapshots={total} (current version {FEATURE_VERSION}: {current}), "
        f"technical_indicators={ta}, insider_clusters={cl}"
    )


def rebuild_ta(session, end: date, batch: int) -> int:
    from trading_signals.derived.technical_indicators import (
        TechnicalIndicatorsComputer,
    )
    from trading_signals.utils.retention import data_start_date

    ta = TechnicalIndicatorsComputer(session)
    ta._ensure_spy()
    tickers = ta._tickers_with_prices(data_start_date(), end)
    total = 0
    for i, ticker in enumerate(tickers, 1):
        try:
            # SAVEPOINT: a bad ticker must not abort the batch
            with session.begin_nested():
                total += ta._compute_backfill(ticker)
        except Exception as e:  # keep going
            logger.error(f"TA {ticker} failed: {e}")
            continue
        if i % batch == 0:
            session.commit()
            logger.info(f"TA {i}/{len(tickers)} tickers, {total} rows")
    session.commit()
    logger.info(f"TA done: {total} rows for {len(tickers)} tickers")
    return total


def rebuild_clusters(session) -> int:
    from trading_signals.derived.insider_clusters import InsiderClusterComputer

    n = InsiderClusterComputer(session).rebuild_all()
    session.commit()
    logger.info(f"insider_clusters rebuilt: {n}")
    return n


def rebuild_features(session, sessions: list[date], commit_every: int) -> int:
    from sqlalchemy import delete

    from trading_signals.db.models.features import FeatureSnapshot
    from trading_signals.derived.feature_pipeline import FeaturePipeline

    pipeline = FeaturePipeline(session)
    total = 0
    t_start = time.time()
    for i, d in enumerate(sessions, 1):
        session.execute(
            delete(FeatureSnapshot).where(FeatureSnapshot.snapshot_date == d)
        )
        total += pipeline.compute_daily(d)
        if i % commit_every == 0 or i == len(sessions):
            session.commit()
            per = (time.time() - t_start) / i
            remaining = (len(sessions) - i) * per / 3600
            logger.info(
                f"features committed through {d} ({i}/{len(sessions)}), "
                f"~{remaining:.1f} h remaining"
            )
    return total


def rebuild_targets(session, batch: int) -> int:
    from sqlalchemy import select

    from trading_signals.db.models.features import FeatureSnapshot
    from trading_signals.derived.target_backfill import TargetBackfillComputer

    tickers = [
        r[0]
        for r in session.execute(
            select(FeatureSnapshot.ticker).distinct().order_by(FeatureSnapshot.ticker)
        ).all()
    ]
    comp = TargetBackfillComputer(session)
    total = 0
    for i in range(0, len(tickers), batch):
        total += comp.recompute_tickers(tickers[i : i + batch])
        session.commit()
        logger.info(f"targets {min(i + batch, len(tickers))}/{len(tickers)} tickers")
    return total


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")

    from trading_signals.db.session import get_session

    start, end = resolve_window(args)
    sessions = plan_sessions(start, end)
    lo, hi = (len(sessions) * s / 3600 for s in SECONDS_PER_SESSION)
    logger.info(
        f"Window {start} → {end}: {len(sessions)} NYSE sessions "
        f"(estimated {lo:.1f}-{hi:.1f} h for step 4)"
    )

    with get_session() as session:
        report_counts(session)
        if not args.apply:
            logger.info("DRY RUN – nothing written. Re-run with --apply.")
            return 0
        if not args.skip_ta:
            rebuild_ta(session, end, args.batch)
        if not args.skip_clusters:
            rebuild_clusters(session)
        if not args.skip_features:
            rebuild_features(session, sessions, max(1, args.commit_every))
        if not args.skip_targets:
            rebuild_targets(session, args.batch)
        report_counts(session)
    logger.info("Feature store rebuild finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
