"""Historical validation of the neutral candidate screen (plan 2026-10-17, step 2).

Applies the quality gate and the screen of ``trading_signals.analysis.screen``
to every snapshot date of the ML window and compares the bullish bucket, the
bearish bucket, the random controls and the gated universe on ``return_20d`` /
``return_60d`` - absolute (raw) and relative (excess over the same-day gated
universe and over SPY) - with 95 % batch-jackknife intervals (batches of 2 x
horizon dates), hit rates against the base rate and a per-year table. Output:
``screen_backtest_<date>.md``.

Read-only - nothing is written to the database. The screen definition is NOT
tuned on this output (signs come from the literature, ``docs/SCREEN.md``).

Usage (inside the container):
    .venv/bin/python scripts/analysis/screen_backtest.py --out /app/repair_logs
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("analysis.screen_backtest")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--start", type=date.fromisoformat, default=None,
                   help="First snapshot date (default: ml_start_date())")
    p.add_argument("--end", type=date.fromisoformat, default=None,
                   help="Last snapshot date (default: all)")
    p.add_argument("--no-spy", action="store_true",
                   help="Skip the SPY-relative evaluation")
    p.add_argument("--out", default="reports", help="Output directory")
    return p.parse_args(argv)


def load_features(session, start: date, end: date | None) -> pd.DataFrame:
    """Screen inputs + forward returns of the window (numerics cast in SQL).

    Only the columns the screen reads are selected (scored features, event
    features, gate columns, ``return_20d`` / ``return_60d``) - lighter than
    ``feature_report.build_load_query``, which loads all 97 features and the
    targets as Decimal objects. Column names come from model constants.
    """
    from sqlalchemy import Boolean, text

    from trading_signals.analysis.screen import (
        COL_DOLLAR_VOLUME,
        COL_HISTORY_PROXY,
        DEFAULT_CONFIG,
        scored_features,
    )
    from trading_signals.db.models.features import FEATURE_COLUMNS, FeatureSnapshot

    existing = set(session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'signals' AND table_name = 'feature_snapshots'"
    )).scalars().all())
    table = FeatureSnapshot.__table__
    wanted = dict.fromkeys([
        *scored_features(DEFAULT_CONFIG.groups),
        *(r.feature for r in DEFAULT_CONFIG.events),
        COL_DOLLAR_VOLUME, COL_HISTORY_PROXY,
    ])
    feats = [c for c in wanted if c in FEATURE_COLUMNS and c in existing]
    missing = sorted(set(wanted) - set(feats))
    if missing:
        logger.warning(f"feature_snapshots lacks columns: {missing}")
    exprs = []
    for c in feats:
        cast = "::int::float8" if isinstance(table.c[c].type, Boolean) else "::float8"
        exprs.append(f"{c}{cast} AS {c}")
    targets = [f"return_{h}d::float8 AS return_{h}d" for h in (20, 60)]
    sql = (
        "SELECT snapshot_date, ticker, " + ", ".join([*exprs, *targets])
        + " FROM signals.feature_snapshots WHERE snapshot_date >= :start"
        + (" AND snapshot_date <= :end" if end else "")
    )
    params = {"start": start, **({"end": end} if end else {})}
    df = pd.read_sql(text(sql), session.bind, params=params)
    for c in feats:
        df[c] = df[c].astype("float32")
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    return df


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")

    from trading_signals.analysis.benchmark import load_benchmark_returns
    from trading_signals.analysis.screen_backtest import (
        render_report,
        run_screen_backtest,
    )
    from trading_signals.db.session import get_session
    from trading_signals.utils.retention import ml_start_date

    start = args.start or ml_start_date()
    with get_session() as session:
        features = load_features(session, start, args.end)
        spy = None if args.no_spy else load_benchmark_returns(session, start)
    logger.info(f"{len(features)} snapshots ({features['snapshot_date'].nunique()} "
                f"dates) from {start}; SPY returns: "
                f"{'off' if spy is None else len(spy)}")
    if features.empty:
        logger.error("No feature snapshots - abort")
        return 1

    result = run_screen_backtest(features, spy=spy)
    report = render_report(result, date.today())
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"screen_backtest_{date.today():%Y-%m-%d}.md"
    out_file.write_text(report, encoding="utf-8")
    print(report)
    logger.info(f"Report written to {out_file.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
