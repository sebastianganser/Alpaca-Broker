"""Backfill the FINRA short-interest features (migration 036).

Why: the nightly pipeline only fills ``short_interest_days_to_cover`` and
``short_interest_change_1m`` for the new session. History is computed here
from ``signals.short_interest`` with the SAME rules as the pipeline
(``derived/short_interest_features.py``: a settlement date is usable 9 NYSE
sessions later, max age 40 days) - vectorised via ``merge_asof``. Only these
two columns of existing snapshots are updated; all other features and
``feature_version`` stay as they are. Rows whose value would not change are
skipped, so the script is idempotent (a second run reports 0 changes).

Settlements are read from ``data_start_date() - SI_LOAD_WINDOW_DAYS`` (the
margin lets the first snapshots of the retention window see the settlements
that precede them, exactly like the pipeline); nothing is collected here.

Do not run it while the nightly chain writes snapshots (04:30-05:00,
deadlock risk).

Usage (inside the container):
    .venv/bin/python scripts/repair/backfill_short_interest_features.py
    .venv/bin/python scripts/repair/backfill_short_interest_features.py --apply

The first call is a dry run: it only reports coverage per quarter (DB now vs.
projected) and how many snapshots would change.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta

import pandas as pd

from trading_signals.derived.short_interest_features import (
    SETTLEMENT_COLUMNS,
    SHORT_INTEREST_COLUMNS,
    SI_LOAD_WINDOW_DAYS,
    compute_short_interest_frame,
)
from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("repair.short_interest_backfill")

DTC_COL, CHG_COL = SHORT_INTEREST_COLUMNS
#: Columns holding the values currently stored in the DB.
CUR_DTC, CUR_CHG = "_cur_dtc", "_cur_chg"
#: Rows per ``UPDATE ... FROM (VALUES ...)`` statement.
UPDATE_CHUNK = 5000
#: Tolerance when comparing stored and computed values (NUMERIC(10,4)).
TOLERANCE = 5e-5

COVERAGE_SQL = """
SELECT date_trunc('quarter', snapshot_date)::date AS quarter,
       count(*) AS n,
       count(short_interest_days_to_cover) AS dtc,
       count(short_interest_change_1m) AS chg
FROM signals.feature_snapshots
WHERE snapshot_date >= :start
GROUP BY 1
ORDER BY 1
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--apply", action="store_true", help="Write to the DB (default: dry run)"
    )
    p.add_argument("--batch", type=int, default=100, help="Tickers per commit")
    return p.parse_args(argv)


def quarter_label(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def coverage_by_quarter(session, start: date) -> dict[str, tuple[int, int, int]]:
    """``{quarter: (snapshots, days_to_cover set, change_1m set)}`` from the DB."""
    from sqlalchemy import text

    return {
        quarter_label(r.quarter): (int(r.n), int(r.dtc), int(r.chg))
        for r in session.execute(text(COVERAGE_SQL), {"start": start}).all()
    }


def differs(current: pd.Series, new: pd.Series) -> pd.Series:
    """Mask: stored value != computed value (NaN equals NaN)."""
    both_missing = current.isna() & new.isna()
    close = (current - new).abs() < TOLERANCE  # False if either side is NaN
    return ~(both_missing | close)


def plan_updates(
    keys: pd.DataFrame, settlements: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute the features for ``keys`` and select the rows that change.

    Args:
        keys: ``ticker``, ``snapshot_date`` + the stored values in
            :data:`CUR_DTC` / :data:`CUR_CHG` (float, NaN = NULL).
        settlements: Columns :data:`SETTLEMENT_COLUMNS`.

    Returns:
        ``(merged, todo)``: all keys with the new values and the subset
        whose stored value differs (nothing to write when already current).
    """
    computed = compute_short_interest_frame(
        settlements, keys[["ticker", "snapshot_date"]]
    )
    merged = keys.merge(computed, on=["ticker", "snapshot_date"], how="left")
    mask = differs(merged[CUR_DTC], merged[DTC_COL]) | differs(
        merged[CUR_CHG], merged[CHG_COL]
    )
    return merged, merged[mask]


def update_rows(todo: pd.DataFrame) -> list[tuple]:
    """``(snapshot_date, ticker, days_to_cover, change_1m)``; NaN -> None (NULL)."""

    def _val(x):
        return None if pd.isna(x) else float(x)

    return [
        (r["snapshot_date"], r["ticker"], _val(r[DTC_COL]), _val(r[CHG_COL]))
        for r in todo.to_dict("records")
    ]


def build_update(rows: list[tuple]):
    """``UPDATE feature_snapshots ... FROM (VALUES ...)`` for ``rows``.

    Sets only the two short-interest columns (``feature_version`` and all
    other features stay untouched).
    """
    from sqlalchemy import Date, Numeric, String, cast, column, update, values

    from trading_signals.db.models.features import FeatureSnapshot

    table = FeatureSnapshot.__table__
    v = values(
        column("snapshot_date", Date),
        column("ticker", String),
        column("dtc", Numeric(10, 4)),
        column("chg", Numeric(10, 4)),
        name="v",
    ).data(rows)
    return (
        update(table)
        .where(table.c.snapshot_date == v.c.snapshot_date)
        .where(table.c.ticker == v.c.ticker)
        .values(
            {
                DTC_COL: cast(v.c.dtc, Numeric(10, 4)),
                CHG_COL: cast(v.c.chg, Numeric(10, 4)),
            }
        )
    )


def apply_updates(session, todo: pd.DataFrame) -> int:
    """Write ``todo`` in batches of :data:`UPDATE_CHUNK`; returns rows sent."""
    rows = update_rows(todo)
    for i in range(0, len(rows), UPDATE_CHUNK):
        session.execute(build_update(rows[i : i + UPDATE_CHUNK]))
    return len(rows)


def print_coverage(before: dict, after: dict, after_label: str) -> None:
    """Filled share per quarter (before = DB, after = ``after_label``)."""

    def pct(filled: int, n: int) -> str:
        return f"{100.0 * filled / n:5.1f}%" if n else "    - "

    print(f"\n  Coverage per quarter (before = DB, after = {after_label})")
    print(
        f"  {'quarter':<8} {'snapshots':>10} | "
        f"{'days_to_cover':^17} | {'change_1m':^17}"
    )
    for q in sorted(before.keys() | after.keys()):
        n, d0, c0 = before.get(q, (0, 0, 0))
        n2, d1, c1 = after.get(q, (n, 0, 0))
        print(
            f"  {q:<8} {n:>10} | {pct(d0, n):>7} -> {pct(d1, n2):>7} "
            f"| {pct(c0, n):>7} -> {pct(c1, n2):>7}"
        )
    print()


def load_chunk(session, tickers: list[str], start: date):
    """Snapshot keys with stored values + settlement rows of ``tickers``."""
    from sqlalchemy import select

    from trading_signals.db.models.features import FeatureSnapshot
    from trading_signals.db.models.short_interest import ShortInterest

    keys = pd.DataFrame(
        [
            tuple(r)
            for r in session.execute(
                select(
                    FeatureSnapshot.ticker,
                    FeatureSnapshot.snapshot_date,
                    FeatureSnapshot.short_interest_days_to_cover,
                    FeatureSnapshot.short_interest_change_1m,
                )
                .where(FeatureSnapshot.ticker.in_(tickers))
                .where(FeatureSnapshot.snapshot_date >= start)
            ).all()
        ],
        columns=["ticker", "snapshot_date", CUR_DTC, CUR_CHG],
    )
    for col in (CUR_DTC, CUR_CHG):
        keys[col] = pd.to_numeric(keys[col], errors="coerce").astype(float)
    settlements = pd.DataFrame(
        [
            tuple(r)
            for r in session.execute(
                select(*(getattr(ShortInterest, c) for c in SETTLEMENT_COLUMNS))
                .where(ShortInterest.ticker.in_(tickers))
                .where(
                    ShortInterest.settlement_date
                    >= start - timedelta(days=SI_LOAD_WINDOW_DAYS)
                )
                .order_by(
                    ShortInterest.ticker,
                    ShortInterest.settlement_date,
                    ShortInterest.fetched_at,
                )
            ).all()
        ],
        columns=list(SETTLEMENT_COLUMNS),
    )
    return keys, settlements


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")

    from sqlalchemy import func, select

    from trading_signals.db.models.features import FeatureSnapshot
    from trading_signals.db.models.short_interest import ShortInterest
    from trading_signals.db.session import get_session
    from trading_signals.utils.retention import data_start_date

    start = data_start_date()
    batch = max(1, args.batch)
    projected: dict[str, list[int]] = {}
    changed_rows = 0
    total_rows = 0

    with get_session() as session:
        n_settlements = session.execute(
            select(func.count()).select_from(ShortInterest)
        ).scalar()
        tickers = [
            r[0]
            for r in session.execute(
                select(FeatureSnapshot.ticker)
                .where(FeatureSnapshot.snapshot_date >= start)
                .distinct()
                .order_by(FeatureSnapshot.ticker)
            ).all()
        ]
        logger.info(
            f"window from {start}: {len(tickers)} tickers with snapshots, "
            f"short_interest rows={n_settlements}"
        )
        if args.apply and not n_settlements:
            logger.error("short_interest is empty - nothing to compute, aborting.")
            return 1
        before = coverage_by_quarter(session, start)

        t0 = time.time()
        for i in range(0, len(tickers), batch):
            keys, settlements = load_chunk(session, tickers[i : i + batch], start)
            merged, todo = plan_updates(keys, settlements)

            for q, grp in merged.groupby(merged["snapshot_date"].map(quarter_label)):
                acc = projected.setdefault(q, [0, 0, 0])
                acc[0] += len(grp)
                acc[1] += int(grp[DTC_COL].notna().sum())
                acc[2] += int(grp[CHG_COL].notna().sum())

            total_rows += len(merged)
            changed_rows += len(todo)
            if args.apply and len(todo):
                apply_updates(session, todo)
                session.commit()
            logger.info(
                f"short-interest {min(i + batch, len(tickers))}/{len(tickers)} "
                f"tickers, {total_rows} snapshots checked, {changed_rows} "
                f"{'updated' if args.apply else 'would change'}, "
                f"{time.time() - t0:.0f}s"
            )

        if args.apply:
            print_coverage(before, coverage_by_quarter(session, start), "DB")
        else:
            after = {q: tuple(v) for q, v in projected.items()}
            print_coverage(before, after, "projected")
            logger.info("DRY RUN - nothing written. Re-run with --apply.")
    logger.info(
        f"Short-interest feature backfill finished ({changed_rows} rows changed)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
