"""Recompute ONLY the four politician columns of ``feature_snapshots``.

Why: after a new politician-trade source (House of Representatives) has been
backfilled, the historical ``politician_*`` features must be recomputed so that
history and future are consistent. A full feature rebuild
(``features_rebuild.py``) takes 7-12 hours; this script recomputes just these
four columns in minutes with the vectorised
``derived/politician_features.compute_politician_features`` (same semantics as
``FeaturePipeline._politician_features``).

What it does, per chunk of ``--batch`` tickers:

1. loads the ticker's trades (ALL stored trades - whether a ticker is "covered"
   depends on any earlier trade) and the existing snapshot keys with their
   current four values for ``--start``..``--end``;
2. computes the new values for exactly the existing snapshot sessions and diffs
   them NULL-aware against the stored ones;
3. ``--apply`` only: updates ONLY the changed rows (temp table + one
   ``UPDATE ... FROM``, no per-row round trips), one commit per chunk.

Only existing snapshot rows are updated; ``computed_at`` and ``feature_version``
are NOT touched (this is a column repair, not a new feature version). Rows that
the nightly pipeline would create or delete because *all* features became
non-NULL / NULL are not created or deleted here (see the summary line "not
handled"). Idempotent: a re-run changes 0 rows.

Verification (always, before anything is written): N seeded random snapshot
keys (half covered, half not covered where available) are compared against the
real ``FeaturePipeline._politician_features``. Any mismatch is printed, makes
the exit code non-zero and ``--apply`` refuses to write.

Dry run (default) is read-only (``SET default_transaction_read_only = on``).
Time guard: refuses to run 04:15-05:15 Europe/Berlin (the nightly chain writes
``feature_snapshots`` 04:30-05:00, deadlock risk).

Usage (inside the container, ``alpaca-ops run`` / ``run-bg``)::

    scripts/repair/recompute_politician_features.py                 # dry run
    scripts/repair/recompute_politician_features.py --verify 500
    scripts/repair/recompute_politician_features.py --tickers AAPL MSFT
    scripts/repair/recompute_politician_features.py --apply
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import pandas as pd

from trading_signals.derived.politician_features import (
    POLITICIAN_FEATURE_COLUMNS,
    compute_politician_features,
)
from trading_signals.utils.logging import setup_logging
from trading_signals.utils.repair_support import announce_db_target, out, repair_session

SCRIPT = "recompute_politician_features"

BERLIN = ZoneInfo("Europe/Berlin")
#: The nightly chain writes feature_snapshots 04:30-05:00 Berlin time.
BLOCK_START = dtime(4, 15)
BLOCK_END = dtime(5, 15)

DEFAULT_VERIFY = 200
DEFAULT_BATCH = 25
DEFAULT_SEED = 20261109
MAX_EXAMPLES = 10
#: Tickers whose rows are loaded for the verification sample.
VERIFY_TICKERS_WITH_TRADES = 20
VERIFY_TICKERS_WITHOUT_TRADES = 10

COLS = list(POLITICIAN_FEATURE_COLUMNS)


# ── pure helpers ─────────────────────────────────────────────────────


def in_blocked_window(now: datetime) -> bool:
    """True inside 04:15 <= t < 05:15 Berlin time (``now`` must be aware)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    t = now.astimezone(BERLIN).time()
    return BLOCK_START <= t < BLOCK_END


def changed_mask(current: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Boolean frame, True where ``current`` and ``new`` differ (NULL-aware).

    Both frames share the same index and the four columns. NA == NA is
    "unchanged"; NA vs a number (either way) is "changed".
    """
    mask = pd.DataFrame(False, index=new.index, columns=COLS)
    for c in COLS:
        cur_na = current[c].isna().to_numpy()
        new_na = new[c].isna().to_numpy()
        cur_v = current[c].fillna(0).astype("int64").to_numpy()
        new_v = new[c].fillna(0).astype("int64").to_numpy()
        mask[c] = (cur_na != new_na) | (~cur_na & ~new_na & (cur_v != new_v))
    return mask


@dataclass
class Stats:
    """Accumulated diff statistics of a run."""

    scanned: int = 0
    changed_rows: int = 0
    changed: Counter = field(default_factory=Counter)
    null_to_value: Counter = field(default_factory=Counter)
    value_to_null: Counter = field(default_factory=Counter)
    per_year: Counter = field(default_factory=Counter)
    examples: list[str] = field(default_factory=list)

    def add(
        self, ticker: str, current: pd.DataFrame, new: pd.DataFrame
    ) -> pd.DataFrame:
        """Fold one ticker in; returns the change mask."""
        mask = changed_mask(current, new)
        self.scanned += len(new)
        any_changed = mask.any(axis=1)
        self.changed_rows += int(any_changed.sum())
        for c in COLS:
            m = mask[c]
            self.changed[c] += int(m.sum())
            self.null_to_value[c] += int((m & current[c].isna() & new[c].notna()).sum())
            self.value_to_null[c] += int((m & current[c].notna() & new[c].isna()).sum())
        for d in new.index[any_changed.to_numpy()]:
            self.per_year[pd.Timestamp(d).year] += 1
            if len(self.examples) < MAX_EXAMPLES:
                diffs = ", ".join(
                    f"{c.removeprefix('politician_')}: "
                    f"{_fmt(current.loc[d, c])} -> {_fmt(new.loc[d, c])}"
                    for c in COLS
                    if mask.loc[d, c]
                )
                self.examples.append(f"{ticker} {d}: {diffs}")
        return mask

    def lines(self) -> list[str]:
        out_lines = [
            f"rows scanned: {self.scanned}",
            f"rows that would change: {self.changed_rows}",
            "per column (changed / NULL->value / value->NULL):",
        ]
        for c in COLS:
            out_lines.append(
                f"  {c}: {self.changed[c]} / {self.null_to_value[c]} / "
                f"{self.value_to_null[c]}"
            )
        out_lines.append("changed rows per year:")
        for y in sorted(self.per_year):
            out_lines.append(f"  {y}: {self.per_year[y]}")
        if self.examples:
            out_lines.append("example diffs:")
            out_lines.extend(f"  {e}" for e in self.examples)
        return out_lines


def _fmt(v: object) -> str:
    return "NULL" if pd.isna(v) else str(int(v))


def pick_verification_keys(
    frames: dict[str, pd.DataFrame], n: int, rng: random.Random
) -> list[tuple[str, date]]:
    """Up to ``n`` (ticker, session) keys, half covered / half not covered.

    ``frames`` maps ticker -> computed features (index = sessions). If one
    class has too few keys the other one fills up.
    """
    covered: list[tuple[str, date]] = []
    uncovered: list[tuple[str, date]] = []
    for ticker, df in frames.items():
        is_cov = df[COLS].notna().all(axis=1).to_numpy()
        for d, cov in zip(df.index, is_cov, strict=True):
            (covered if cov else uncovered).append((ticker, d))
    half = n // 2
    take_c = min(len(covered), max(half, n - len(uncovered)))
    take_u = min(len(uncovered), n - take_c)
    return rng.sample(covered, take_c) + rng.sample(uncovered, take_u)


def expected_row(features: dict) -> dict[str, int | None]:
    """Pipeline result (``{}`` = not covered) as a full four-column row."""
    return {c: features.get(c) for c in COLS}


def row_of(df: pd.DataFrame, d: date) -> dict[str, int | None]:
    return {c: (None if pd.isna(df.loc[d, c]) else int(df.loc[d, c])) for c in COLS}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--start", type=date.fromisoformat, default=None,
                   help="first snapshot date (default: data_start_date())")
    p.add_argument("--end", type=date.fromisoformat, default=None,
                   help="last snapshot date (default: last stored snapshot)")
    p.add_argument("--tickers", nargs="+", default=None,
                   help="only these tickers (space or comma separated)")
    p.add_argument("--verify", type=int, default=DEFAULT_VERIFY,
                   help=f"snapshot keys checked against the pipeline "
                        f"(default {DEFAULT_VERIFY})")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED,
                   help="seed of the verification sample")
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                   help=f"tickers per commit (default {DEFAULT_BATCH})")
    p.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = p.parse_args(argv)
    if args.tickers:
        args.tickers = sorted(
            {t.strip().upper() for x in args.tickers for t in x.split(",") if t.strip()}
        )
    if args.batch < 1:
        p.error("--batch must be >= 1")
    return args


# ── database access ──────────────────────────────────────────────────


def load_trades(session, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """All stored trades of the tickers, one frame per ticker."""
    from sqlalchemy import select

    from trading_signals.db.models.politicians import PoliticianTrade as T

    rows = session.execute(
        select(
            T.ticker, T.transaction_date, T.disclosure_date,
            T.transaction_type, T.politician_name,
        ).where(T.ticker.in_(tickers))
    ).all()
    cols = ["ticker", "transaction_date", "disclosure_date",
            "transaction_type", "politician_name"]
    df = pd.DataFrame(rows, columns=cols)
    return {t: g.drop(columns="ticker") for t, g in df.groupby("ticker")}


def load_snapshots(
    session, tickers: list[str], start: date, end: date
) -> dict[str, pd.DataFrame]:
    """Current four values per ticker, indexed by snapshot_date."""
    from sqlalchemy import select

    from trading_signals.db.models.features import FeatureSnapshot as F

    rows = session.execute(
        select(F.ticker, F.snapshot_date, *[getattr(F, c) for c in COLS])
        .where(F.ticker.in_(tickers))
        .where(F.snapshot_date.between(start, end))
        .order_by(F.ticker, F.snapshot_date)
    ).all()
    df = pd.DataFrame(rows, columns=["ticker", "snapshot_date", *COLS])
    result = {}
    for t, g in df.groupby("ticker"):
        g = g.set_index("snapshot_date").drop(columns="ticker")
        result[t] = g.astype({c: object for c in COLS}).astype("Int64")
    return result


def compute_for(
    trades: dict[str, pd.DataFrame], snaps: dict[str, pd.DataFrame]
) -> dict[str, pd.DataFrame]:
    empty = pd.DataFrame(
        columns=["transaction_date", "disclosure_date", "transaction_type",
                 "politician_name"]
    )
    return {
        t: compute_politician_features(trades.get(t, empty), list(cur.index))
        for t, cur in snaps.items()
    }


def snapshot_tickers(session, start: date, end: date) -> list[str]:
    from sqlalchemy import select

    from trading_signals.db.models.features import FeatureSnapshot as F

    return list(
        session.execute(
            select(F.ticker)
            .where(F.snapshot_date.between(start, end))
            .distinct()
            .order_by(F.ticker)
        ).scalars()
    )


def trade_tickers(session) -> set[str]:
    from sqlalchemy import select

    from trading_signals.db.models.politicians import PoliticianTrade as T

    return set(session.execute(select(T.ticker).distinct()).scalars())


def last_snapshot_date(session) -> date | None:
    from sqlalchemy import func, select

    from trading_signals.db.models.features import FeatureSnapshot as F

    return session.execute(select(func.max(F.snapshot_date))).scalar()


def verify(
    session, tickers: list[str], with_trades: set[str], n: int, seed: int,
    start: date, end: date,
) -> list[str]:
    """Compare ``n`` sampled keys with the real pipeline; returns mismatch lines."""
    from trading_signals.derived.feature_pipeline import FeaturePipeline

    if n <= 0 or not tickers:
        return []
    rng = random.Random(seed)
    have = [t for t in tickers if t in with_trades]
    lack = [t for t in tickers if t not in with_trades]
    sample = rng.sample(have, min(len(have), VERIFY_TICKERS_WITH_TRADES))
    sample += rng.sample(lack, min(len(lack), VERIFY_TICKERS_WITHOUT_TRADES))
    snaps = load_snapshots(session, sample, start, end)
    frames = compute_for(load_trades(session, sample), snaps)
    keys = pick_verification_keys(frames, n, rng)
    pipeline = FeaturePipeline(session)
    mismatches = []
    n_cov = 0
    for ticker, d in keys:
        got = row_of(frames[ticker], d)
        n_cov += got[COLS[0]] is not None
        want = expected_row(pipeline._politician_features(ticker, d))
        if got != want:
            mismatches.append(
                f"MISMATCH {ticker} {d}: vectorised={got} pipeline={want}"
            )
    out(f"verification: {len(keys)} keys from {len(sample)} tickers "
        f"({n_cov} covered, {len(keys) - n_cov} not covered), "
        f"{len(mismatches)} mismatches")
    return mismatches


def write_changes(session, ticker: str, new: pd.DataFrame, mask: pd.DataFrame) -> int:
    """UPDATE the changed rows via a temp table; returns the rows updated."""
    from sqlalchemy import Column, Date, Integer, MetaData, String, Table, insert, text

    rows_mask = mask.any(axis=1).to_numpy()
    if not rows_mask.any():
        return 0
    changed = new[rows_mask]
    payload = [
        {
            "snapshot_date": d,
            "ticker": ticker,
            **{
                c: (None if pd.isna(v) else int(v))
                for c, v in zip(COLS, vals, strict=True)
            },
        }
        for d, vals in zip(
            changed.index, changed[COLS].itertuples(index=False), strict=True
        )
    ]
    tmp = Table(
        "_pol_recompute", MetaData(),
        Column("snapshot_date", Date), Column("ticker", String(20)),
        *[Column(c, Integer) for c in COLS],
        prefixes=["TEMPORARY"],
    )
    conn = session.connection()
    tmp.create(bind=conn, checkfirst=False)
    try:
        conn.execute(insert(tmp), payload)
        sets = ", ".join(f"{c} = t.{c}" for c in COLS)
        differs = " OR ".join(f"f.{c} IS DISTINCT FROM t.{c}" for c in COLS)
        res = conn.execute(text(
            f"UPDATE signals.feature_snapshots f SET {sets} FROM _pol_recompute t "
            f"WHERE f.snapshot_date = t.snapshot_date AND f.ticker = t.ticker "
            f"AND ({differs})"
        ))
        updated = res.rowcount
    finally:
        tmp.drop(bind=conn, checkfirst=False)
    return updated


# ── main ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")
    announce_db_target(SCRIPT)
    now = datetime.now(BERLIN)
    if in_blocked_window(now):
        out(
            f"REFUSED: {now:%H:%M} Berlin time is inside the nightly chain "
            f"window ({BLOCK_START:%H:%M}-{BLOCK_END:%H:%M}); feature_snapshots "
            "is being written"
        )
        return 1
    from sqlalchemy import text

    from trading_signals.utils.retention import data_start_date

    out(f"[{SCRIPT}] mode: {'APPLY' if args.apply else 'dry run'}")
    started = time.monotonic()
    with repair_session(read_only=not args.apply) as session:
        if not args.apply:
            session.execute(text("SET default_transaction_read_only = on"))
        start = args.start or data_start_date()
        end = args.end or last_snapshot_date(session)
        if end is None:
            out("REFUSED: no feature snapshots stored")
            return 1
        tickers = snapshot_tickers(session, start, end)
        if args.tickers:
            unknown = sorted(set(args.tickers) - set(tickers))
            if unknown:
                out(f"note: no snapshots in range for {', '.join(unknown)}")
            tickers = [t for t in tickers if t in set(args.tickers)]
        out(f"range {start} .. {end}, {len(tickers)} tickers, batch {args.batch}")

        mismatches = verify(
            session, tickers, trade_tickers(session), args.verify, args.seed, start, end
        )
        for line in mismatches:
            out(line)
        if mismatches and args.apply:
            out("REFUSED: verification failed, nothing written")
            return 1

        stats = Stats()
        updated_total = 0
        for i in range(0, len(tickers), args.batch):
            chunk = tickers[i : i + args.batch]
            snaps = load_snapshots(session, chunk, start, end)
            news = compute_for(load_trades(session, chunk), snaps)
            chunk_updated = 0
            for ticker, cur in snaps.items():
                mask = stats.add(ticker, cur, news[ticker])
                if args.apply:
                    chunk_updated += write_changes(session, ticker, news[ticker], mask)
            if args.apply:
                session.commit()
                updated_total += chunk_updated
            out(f"  {min(i + args.batch, len(tickers))}/{len(tickers)} tickers, "
                f"changed so far {stats.changed_rows}"
                + (f", updated {updated_total}" if args.apply else "")
                + f" ({time.monotonic() - started:.0f}s)")

    for line in stats.lines():
        out(line)
    out("not handled: snapshot rows that the nightly pipeline would create (all other "
        "features NULL, politician value appears) or delete (politician values were "
        "the only non-NULL features); computed_at / feature_version are unchanged")
    if args.apply:
        out(f"applied: {updated_total} rows updated (planned {stats.changed_rows})")
        if updated_total != stats.changed_rows:
            out("WARNING: updated rows differ from the plan (concurrent writer?)")
    else:
        out(
            "dry run done: nothing written; --apply would update "
            f"{stats.changed_rows} rows"
        )
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
