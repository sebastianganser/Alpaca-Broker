"""Backfill ``analyst_ratings.price_target_new / price_target_old`` from ``raw_data``.

Why: the yfinance mapping stored both price-target columns as NULL for every
row, although Yahoo's ``upgrades_downgrades`` rows carry ``currentPriceTarget``
and ``priorPriceTarget`` (about 98 % of the stored rows, kept as text in the
``raw_data`` JSONB). The mapping is fixed for new rows
(``YFinanceClient.fetch_analyst_ratings``); this script fills the history.

Rules (pure parsing in ``utils/price_targets.py``):

* only a column that is still NULL is filled; a stored value is never changed,
  and the UPDATE itself is guarded with ``COALESCE(column, new)``, so a row the
  collector wrote in the meantime cannot be overwritten;
* ``0``, NaN, ``"nan"``, ``"None"`` and absurd values are no price target and
  leave the column NULL (NULL is not 0);
* idempotent: a second run finds nothing to change.

Do not run it while the nightly collectors write ``analyst_ratings`` (01:00,
see docs/SCHEDULER.md); ``alpaca-ops run-bg`` refuses to start inside the
night window anyway.

Usage (inside the container, via ``alpaca-ops run`` / ``run-bg``):
    .venv/bin/python scripts/repair/backfill_analyst_price_targets.py
    .venv/bin/python scripts/repair/backfill_analyst_price_targets.py --apply

The first call is a dry run in a READ ONLY transaction: it reports how many
rows would change and why the rest does not.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from trading_signals.utils.logging import get_logger, setup_logging
from trading_signals.utils.price_targets import (
    KEY_NEW,
    KEY_OLD,
    parse_price_target,
)
from trading_signals.utils.repair_support import (
    announce_db_target,
    out,
    repair_session,
)

logger = get_logger("repair.backfill_analyst_price_targets")

#: Rows read (and, with ``--apply``, written and committed) per round trip.
DEFAULT_BATCH = 2000
#: Rows per ``UPDATE ... FROM (VALUES ...)`` statement.
UPDATE_CHUNK = 1000


@dataclass
class Stats:
    """What a run found (and changed)."""

    scanned: int = 0  # rows with at least one NULL target column
    fill_new: int = 0
    fill_old: int = 0
    rows_to_update: int = 0
    #: raw_data holds no price-target key at all (or the row has no raw_data)
    no_raw_target: int = 0
    #: a key is present but is no usable target ("nan", "0.0", "None", ...)
    unusable_raw_target: int = 0
    samples: list[tuple[int, float | None, float | None]] = field(default_factory=list)


@dataclass(frozen=True)
class Fill:
    """The values to write into one row (None = leave that column alone)."""

    row_id: int
    new: float | None
    old: float | None


def plan_row(
    row_id: int,
    current_new: object,
    current_old: object,
    raw_new: object,
    raw_old: object,
) -> Fill | None:
    """What to write into one row, or ``None`` if nothing changes.

    A column that already holds a value is left alone; a NULL column is filled
    with the parsed ``raw_data`` text if that is a usable price target.
    """
    new = old = None
    if current_new is None:
        new = parse_price_target(raw_new)
    if current_old is None:
        old = parse_price_target(raw_old)
    if new is None and old is None:
        return None
    return Fill(row_id, new, old)


def classify_missing(
    current_new: object, current_old: object, raw_new: object, raw_old: object
) -> str | None:
    """Why a row with a NULL column gets nothing: ``"no_raw"`` / ``"unusable"``.

    ``None`` if the row is not a gap (both columns set) or can be filled.
    """
    wanted = []
    if current_new is None:
        wanted.append(raw_new)
    if current_old is None:
        wanted.append(raw_old)
    if not wanted:
        return None
    if any(parse_price_target(r) is not None for r in wanted):
        return None
    return "no_raw" if all(r is None for r in wanted) else "unusable"


def plan_batch(rows: Sequence[Sequence[object]], stats: Stats) -> list[Fill]:
    """Plan one batch of ``(id, target_new, target_old, raw_new, raw_old)`` rows."""
    fills: list[Fill] = []
    for row_id, cur_new, cur_old, raw_new, raw_old in rows:
        stats.scanned += 1
        fill = plan_row(int(row_id), cur_new, cur_old, raw_new, raw_old)  # type: ignore[arg-type]
        if fill is None:
            reason = classify_missing(cur_new, cur_old, raw_new, raw_old)
            if reason == "no_raw":
                stats.no_raw_target += 1
            elif reason == "unusable":
                stats.unusable_raw_target += 1
            continue
        stats.rows_to_update += 1
        stats.fill_new += fill.new is not None
        stats.fill_old += fill.old is not None
        if len(stats.samples) < 5:
            stats.samples.append((fill.row_id, fill.new, fill.old))
        fills.append(fill)
    return fills


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--apply", action="store_true", help="Write to the DB (default: dry run)"
    )
    p.add_argument(
        "--batch",
        type=int,
        default=DEFAULT_BATCH,
        help=f"Rows per batch and commit (default {DEFAULT_BATCH})",
    )
    return p.parse_args(argv)


# ── database access ──────────────────────────────────────────────────


def fetch_batch(session, last_id: int, limit: int) -> list[tuple]:
    """Next ``limit`` rows after ``last_id`` that still have a NULL target column."""
    from sqlalchemy import or_, select

    from trading_signals.db.models.fundamentals import AnalystRating as R

    return [
        tuple(r)
        for r in session.execute(
            select(
                R.id,
                R.price_target_new,
                R.price_target_old,
                R.raw_data[KEY_NEW].astext,
                R.raw_data[KEY_OLD].astext,
            )
            .where(R.id > last_id)
            .where(or_(R.price_target_new.is_(None), R.price_target_old.is_(None)))
            .order_by(R.id)
            .limit(limit)
        ).all()
    ]


def column_counts(session) -> dict[str, int]:
    """Row counts of the table and of its price-target columns / raw keys."""
    from sqlalchemy import func, select

    from trading_signals.db.models.fundamentals import AnalystRating as R

    row = session.execute(
        select(
            func.count(),
            func.count(R.price_target_new),
            func.count(R.price_target_old),
            func.count(R.raw_data[KEY_NEW].astext),
            func.count(R.raw_data[KEY_OLD].astext),
        )
    ).one()
    keys = ("rows", "new_set", "old_set", "raw_new_key", "raw_old_key")
    return {k: int(v) for k, v in zip(keys, row, strict=True)}


def build_update(fills: Sequence[Fill]):
    """``UPDATE analyst_ratings ... FROM (VALUES ...)`` for ``fills``.

    ``COALESCE(column, new)``: a value that is already there always wins.
    """
    from sqlalchemy import BigInteger, Numeric, cast, column, func, update, values

    from trading_signals.db.models.fundamentals import AnalystRating as R

    v = values(
        column("id", BigInteger),
        column("pt_new", Numeric(16, 4)),
        column("pt_old", Numeric(16, 4)),
        name="v",
    ).data([(f.row_id, f.new, f.old) for f in fills])
    table = R.__table__
    return (
        update(table)
        .where(table.c.id == v.c.id)
        .values(
            price_target_new=func.coalesce(
                table.c.price_target_new, cast(v.c.pt_new, Numeric(16, 4))
            ),
            price_target_old=func.coalesce(
                table.c.price_target_old, cast(v.c.pt_old, Numeric(16, 4))
            ),
        )
    )


def apply_fills(session, fills: Sequence[Fill]) -> None:
    """Write ``fills`` in statements of at most :data:`UPDATE_CHUNK` rows."""
    for i in range(0, len(fills), UPDATE_CHUNK):
        session.execute(build_update(fills[i : i + UPDATE_CHUNK]))


# ── report ───────────────────────────────────────────────────────────


def print_counts(label: str, counts: dict[str, int]) -> None:
    n = counts["rows"]

    def pct(x: int) -> str:
        return f"{100.0 * x / n:5.1f} %" if n else "    - "

    out(
        f"  {label:<7} rows {n:>8}"
        f" | price_target_new set {counts['new_set']:>8} ({pct(counts['new_set'])})"
        f" | price_target_old set {counts['old_set']:>8} ({pct(counts['old_set'])})"
        f" | raw currentPriceTarget {counts['raw_new_key']:>8}"
        f" ({pct(counts['raw_new_key'])})"
    )


def print_stats(stats: Stats, apply: bool) -> None:
    def line(label: str, value: object, indent: int = 2) -> None:
        out(f"{' ' * indent}{label:<{46 - indent}}{value}")

    unusable = stats.no_raw_target + stats.unusable_raw_target
    line("rows with a NULL target column:", stats.scanned)
    line("rows updated:" if apply else "rows to update:", stats.rows_to_update)
    line("price_target_new filled:", stats.fill_new, 4)
    line("price_target_old filled:", stats.fill_old, 4)
    line("no usable target (left NULL):", unusable)
    line("no price-target key in raw_data:", stats.no_raw_target, 4)
    line("key present, value unusable (0 / nan):", stats.unusable_raw_target, 4)
    for row_id, new, old in stats.samples:
        out(f"    e.g. id {row_id}: new={new} old={old}")


def run(args: argparse.Namespace) -> int:
    batch = max(1, args.batch)
    stats = Stats()
    with repair_session(read_only=not args.apply) as session:
        before = column_counts(session)
        out("")
        print_counts("before", before)
        out("")
        last_id = 0
        while True:
            rows = fetch_batch(session, last_id, batch)
            if not rows:
                break
            last_id = int(rows[-1][0])
            fills = plan_batch(rows, stats)
            if args.apply and fills:
                apply_fills(session, fills)
                session.commit()
            logger.info(
                f"scanned {stats.scanned} rows, {stats.rows_to_update} "
                f"{'updated' if args.apply else 'to update'} (last id {last_id})"
            )
        out("")
        print_stats(stats, args.apply)
        if args.apply:
            after = column_counts(session)
            out("")
            print_counts("after", after)
        else:
            out("")
            out("DRY RUN -- nothing written. Use --apply to write.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")
    announce_db_target("backfill_analyst_price_targets")
    out(f"mode: {'APPLY' if args.apply else 'DRY RUN (read-only)'}")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
