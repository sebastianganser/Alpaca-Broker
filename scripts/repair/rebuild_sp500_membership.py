"""Rebuild the S&P 500 intervals in signals.index_membership (repair step 2a).

Why: until 2026-10 the seed never saw an index EXIT (the change table had moved
to another Wikipedia page, see docs/reports/2026-10-16_sp500_survivorship_inventory.md),
so ``index_membership`` only holds members that were current at the seed:
the point-in-time S&P 500 set differs from the truth on 1,242 of 1,265 trading
days (2022-01-03: 426 instead of 505 members). This script replaces the
``sp500`` rows by the ground truth and leaves every other index (Nasdaq-100)
untouched.

Ground truth
------------
* the CURRENT constituents (Wikipedia "List of S&P 500 companies"), and
* the change table "Historical components of the S&P 500" since 2021-01-01,
  parsed by ``WikipediaIndexHistoryParser.parse_sp500_changes`` (aborts if the
  result is not ``complete``, i.e. has no removals),
* plus the manual events in :data:`MANUAL_EVENTS` (Paramount/Skydance, which
  the table does not list),
* turned into intervals by ``generate_intervals_from_changes``.

Conventions (``universe/ticker_aliases.py``): a ticker rename is ONE continuous
interval under the CURRENT symbol (old symbols such as CDAY, FB, WRK get no
interval); a real exit ends the interval at the effective removal date
(``valid_to`` = first day that is not a member, matching ``valid_to > d`` in
``UniverseManager.get_universe_as_of``). Intervals start at 2021-01-01 at the
earliest (index intervals are the only data allowed to precede
``data_start_date()``).

What a run prints
-----------------
* the diff against the DB, one line per change and ticker
  (``INSERT`` missing, ``UPDATE`` wrong ``valid_from``/``valid_to``,
  ``DELETE`` rows that do not exist in the truth, e.g. PSKY from 2021-01-01),
* the point-in-time member count at the first session of every year and the
  min/max over all NYSE sessions since ``data_start_date()`` -- DB today vs.
  after the repair (expected 503-506 after; sessions outside 495-510 are listed
  and block ``--apply``).

Usage (inside the container; ``alpaca-ops run`` refuses 22:15-05:15; also keep
away from the 1st of the month 03:00 ``index_sync``)::

    PY=.venv/bin/python
    $PY scripts/repair/rebuild_sp500_membership.py              # dry run
    $PY scripts/repair/rebuild_sp500_membership.py --apply
    $PY scripts/repair/rebuild_sp500_membership.py --restore <backup.csv> --apply

``--apply`` does, in ONE transaction: lock the table (blocks a concurrent
``index_sync``), write the backup CSV of ALL old sp500 rows (``--backup-dir``,
default /mnt/user/Workfiles/AlpacaBroker/ops; the run stops if the backup cannot
be written and verified), DELETE, UPDATE, INSERT, re-read and check that the DB
now equals the truth (otherwise ROLLBACK), COMMIT. It refuses to run if more than
``--max-deletes`` (10) rows would be deleted or a session would have a member
count outside 495-510 (``--ignore-validation``). A second ``--apply`` finds
nothing to change (and writes no backup).

Expected effect on the production DB (derived from the 2026-10-16 inventory and
checked against the live Wikipedia pages; the dry run is authoritative):
ground truth 610 intervals (503 open, 107 closed = 92 exits in the window + 15
before it); diff against the 506 sp500 rows: INSERT ~105 (102 closed intervals +
the current members TWLO, VYLR, SKYD), UPDATE 9 (valid_to of TAP, TTD, BLDR, CTVA,
WBD; valid_from of BE, P, ILMN, SW), DELETE 1 (PSKY from 2021-01-01), ~496
unchanged. Member counts at the first session of 2021..2026: 505, 505, 503, 503,
503, 503 (DB today 416, 426, 441, 456, 472, 490); min 503 / max 506 over all
1,265 sessions since 2021-10-01, none outside 495-510.

Verify: dry run again (``unchanged 610, INSERT 0, UPDATE 0, DELETE 0``), and
``select count(*) filter (where valid_to is null), count(*) from
signals.index_membership where index_name = 'sp500'`` = 503 / 610.

Rollback: ``--restore <backup.csv> --apply`` makes the sp500 rows equal to the
file again (same diff/transaction logic, backs up the current state first).

Next step: ``scripts/repair/onboard_removed_members.py`` (universe rows + prices
for the members that are now in the table), then the feature rebuild.
"""

from __future__ import annotations

import argparse
import io
import sys
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from trading_signals.universe.ticker_aliases import (
    DEFAULT_ALIASES,
    EVENT_TOLERANCE_DAYS,
)
from trading_signals.universe.wikipedia_index_history import (
    SP500_CURRENT_URL,
    IndexChange,
    ParseResult,
    WikipediaIndexHistoryParser,
    clean_ticker,
    fetch_html,
    generate_intervals_from_changes,
)
from trading_signals.utils.logging import get_logger, setup_logging
from trading_signals.utils.repair_support import (
    BackupError,
    announce_db_target,
    out,
    read_csv_backup,
    repair_session,
    write_csv_backup,
)

logger = get_logger("repair.rebuild_sp500_membership")

INDEX_NAME = "sp500"
#: Earliest start of an index interval (older members get this date).
HISTORY_START = date(2021, 1, 1)
#: Plausible point-in-time member count (inclusive); anything else blocks --apply.
PIT_BAND = (495, 510)
DEFAULT_BACKUP_DIR = "/mnt/user/Workfiles/AlpacaBroker/ops"
BACKUP_PREFIX = "sp500_membership_backup"
BACKUP_COLUMNS = (
    "id",
    "ticker",
    "index_name",
    "valid_from",
    "valid_to",
    "reason",
    "replaced_by",
    "source",
)
#: ``index_membership.reason`` column length.
REASON_MAX = 200
#: ``--apply`` refuses to delete more rows than this (a mis-parse would show up here).
DEFAULT_MAX_DELETES = 10

#: Index events that are in NEITHER the Wikipedia change table NOR the GitHub
#: snapshots. Source: docs/reports/2026-10-16_sp500_survivorship_inventory.md
#: (section 3 "PARA->PSKY (merger)", section 4.3, section 5 "PSKY ... truth
#: 2025-08-08") and Alpaca (PSKY's first bar is 2025-08-07; ``name_change``
#: PSKY->SKYD effective 2026-10-06, see ``ticker_aliases.KNOWN_RENAMES``).
#: Paramount Global (PARA) was merged into Skydance on 2025-08-07; the successor
#: Paramount Skydance replaced it in the index on 2025-08-08. The successor is
#: keyed by its CURRENT symbol (SKYD, PSKY until 2026-10-06). A manual event is
#: skipped automatically once the Wikipedia table carries the same event.
MANUAL_EVENTS: tuple[IndexChange, ...] = (
    IndexChange(
        change_date=date(2025, 8, 8),
        ticker="PARA",
        index_name=INDEX_NAME,
        action="removed",
        reason="Paramount Global merged into Skydance (successor: Paramount Skydance)",
        replaced_by="SKYD",
        source="manual",
    ),
    IndexChange(
        change_date=date(2025, 8, 8),
        ticker="SKYD",
        index_name=INDEX_NAME,
        action="added",
        reason="Paramount Skydance replaced Paramount Global (PSKY until 2026-10-06)",
        source="manual",
    ),
)


class AbortError(RuntimeError):
    """A precondition failed; nothing was written."""


# ── Data model ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Interval:
    """One membership interval ``[valid_from, valid_to)`` (``valid_to`` None = open)."""

    ticker: str
    valid_from: date
    valid_to: date | None
    reason: str | None = None
    replaced_by: str | None = None
    source: str = "wikipedia"
    id: int | None = None  # set for rows read from the DB

    @property
    def end(self) -> date:
        return self.valid_to or date.max

    def span(self) -> str:
        return f"{self.valid_from} .. {self.valid_to or 'open'}"


@dataclass(frozen=True)
class Update:
    old: Interval
    new: Interval

    def changes(self) -> list[str]:
        parts = []
        if self.old.valid_from != self.new.valid_from:
            parts.append(f"valid_from {self.old.valid_from} -> {self.new.valid_from}")
        if self.old.valid_to != self.new.valid_to:
            parts.append(
                f"valid_to {self.old.valid_to or 'open'} -> "
                f"{self.new.valid_to or 'open'}"
            )
        return parts


@dataclass
class MembershipPlan:
    inserts: list[Interval] = field(default_factory=list)
    updates: list[Update] = field(default_factory=list)
    deletes: list[Interval] = field(default_factory=list)
    unchanged: int = 0

    @property
    def empty(self) -> bool:
        return not (self.inserts or self.updates or self.deletes)


def _clip(text: str | None, limit: int = REASON_MAX) -> str | None:
    return text[:limit] if text else text


def to_intervals(generated: Iterable[dict]) -> list[Interval]:
    """``generate_intervals_from_changes`` dicts -> :class:`Interval` (sorted)."""
    out_: list[Interval] = []
    for g in generated:
        out_.append(
            Interval(
                ticker=g["ticker"],
                valid_from=g["valid_from"],
                valid_to=g["valid_to"],
                reason=_clip(g.get("reason")),
                replaced_by=g.get("replaced_by"),
                source=g.get("source") or "wikipedia",
            )
        )
    return sorted(out_, key=lambda iv: (iv.ticker, iv.valid_from))


# ── Ground truth ─────────────────────────────────────────────────────────


@dataclass
class GroundTruth:
    intervals: list[Interval]
    current: set[str]
    result: ParseResult
    manual_applied: list[IndexChange] = field(default_factory=list)
    manual_skipped: list[tuple[IndexChange, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def fetch_current_sp500() -> set[str]:
    """Current S&P 500 symbols from the Wikipedia constituents table."""
    import pandas as pd

    tables = pd.read_html(io.StringIO(fetch_html(SP500_CURRENT_URL)))
    for df in tables:
        cols = {str(c).strip().lower(): c for c in df.columns}
        if "symbol" in cols and len(df) > 100:
            symbols = {clean_ticker(v) for v in df[cols["symbol"]]}
            return {s for s in symbols if s}
    raise AbortError(
        f"constituents table (column 'Symbol') not found on {SP500_CURRENT_URL}"
    )


def _superseding_event(
    manual: IndexChange, parsed: Sequence[IndexChange]
) -> IndexChange | None:
    """A parsed event for the same ticker and action around the manual date."""
    tol = timedelta(days=EVENT_TOLERANCE_DAYS)
    for c in parsed:
        if (
            c.index_name == manual.index_name
            and c.ticker == manual.ticker
            and c.action == manual.action
            and abs(c.change_date - manual.change_date) <= tol
        ):
            return c
    return None


def check_intervals(intervals: Sequence[Interval], current: set[str]) -> list[str]:
    """Internal consistency of the replayed history (empty list = consistent)."""
    problems: list[str] = []
    open_tickers = {iv.ticker for iv in intervals if iv.valid_to is None}
    missing = sorted(current - open_tickers)
    extra = sorted(open_tickers - current)
    if missing:
        problems.append(
            f"{len(missing)} current constituents have no open interval after "
            f"replaying the change table (table and constituent list disagree): "
            f"{missing}"
        )
    if extra:
        problems.append(
            f"{len(extra)} open intervals are not current constituents: {extra}"
        )
    by_ticker: dict[str, list[Interval]] = defaultdict(list)
    for iv in intervals:
        by_ticker[iv.ticker].append(iv)
        if iv.valid_to is not None and iv.valid_to <= iv.valid_from:
            problems.append(f"{iv.ticker}: empty/negative interval {iv.span()}")
    for ticker, ivs in by_ticker.items():
        ivs.sort(key=lambda iv: iv.valid_from)
        for a, b in zip(ivs, ivs[1:], strict=False):
            if b.valid_from < a.end:
                problems.append(
                    f"{ticker}: overlapping intervals {a.span()} / {b.span()}"
                )
    return problems


def build_ground_truth(
    result: ParseResult,
    current: set[str],
    manual_events: Sequence[IndexChange] = MANUAL_EVENTS,
    data_start: date = HISTORY_START,
    today: date | None = None,
) -> GroundTruth:
    """Replay the change table (+ manual events) into membership intervals.

    ``current`` is re-keyed to the current symbol of each company (a constituent
    list that still shows an old symbol must not produce a second interval).
    """
    today = today or date.today()
    truth = GroundTruth(intervals=[], current=set(), result=result)

    for sym in sorted(current):
        mapped = DEFAULT_ALIASES.current_symbol(sym)
        if mapped != sym:
            truth.notes.append(
                f"constituent {sym} re-keyed to its current symbol {mapped}"
            )
        truth.current.add(mapped)

    changes = list(result.changes)
    for event in manual_events:
        if event.change_date < data_start:
            truth.manual_skipped.append((event, f"before {data_start}"))
            continue
        same = _superseding_event(event, result.changes)
        if same is not None:
            truth.manual_skipped.append(
                (event, f"already in the change table ({same.change_date})")
            )
            continue
        truth.manual_applied.append(event)
        changes.append(event)
    changes.sort(key=lambda c: c.change_date)

    generated = generate_intervals_from_changes(
        changes, {INDEX_NAME: set(truth.current)}, data_start
    )
    truth.intervals = to_intervals(
        g for g in generated if g["index_name"] == INDEX_NAME
    )
    truth.problems = check_intervals(truth.intervals, truth.current)
    return truth


def fetch_ground_truth(
    parser: WikipediaIndexHistoryParser | None = None,
    today: date | None = None,
) -> GroundTruth:
    """Fetch both Wikipedia pages and build the ground truth (aborts if unusable)."""
    from trading_signals.universe.index_sync import EXPECTED_COUNTS

    parser = parser or WikipediaIndexHistoryParser()
    result = parser.parse_sp500_changes(since=HISTORY_START)
    if not result.complete:
        raise AbortError(
            "the S&P 500 change table is not complete "
            f"({result.incomplete_reason or 'no removal data'}) -- refusing to rebuild "
            "from an additions-only source; warnings: " + "; ".join(result.warnings)
        )
    current = fetch_current_sp500()
    lo, hi = EXPECTED_COUNTS[INDEX_NAME]
    if not lo <= len(current) <= hi:
        raise AbortError(
            f"implausible number of current constituents: {len(current)} "
            f"(expected {lo}-{hi})"
        )
    return build_ground_truth(result, current, today=today)


# ── Diff ─────────────────────────────────────────────────────────────────


def _overlap_days(a: Interval, b: Interval) -> int:
    start = max(a.valid_from, b.valid_from)
    end = min(a.end, b.end)
    return (end - start).days if end > start else 0


def _pair_up(
    db: list[Interval], truth: list[Interval]
) -> tuple[list[tuple[Interval, Interval]], list[Interval], list[Interval]]:
    """Match DB rows to truth intervals of one ticker.

    Priority: identical span, same ``valid_from``, same ``valid_to``, largest
    overlap. Returns ``(pairs, unmatched_db, unmatched_truth)``.
    """
    d_left, t_left = list(db), list(truth)
    pairs: list[tuple[Interval, Interval]] = []

    def take(match) -> None:
        for d in list(d_left):
            for t in t_left:
                if match(d, t):
                    pairs.append((d, t))
                    d_left.remove(d)
                    t_left.remove(t)
                    break

    take(lambda d, t: d.valid_from == t.valid_from and d.valid_to == t.valid_to)
    take(lambda d, t: d.valid_from == t.valid_from)
    take(lambda d, t: d.valid_to == t.valid_to)
    while d_left and t_left:
        best = max(
            ((_overlap_days(d, t), d, t) for d in d_left for t in t_left),
            key=lambda x: x[0],
        )
        if best[0] <= 0:
            break
        _, d, t = best
        pairs.append((d, t))
        d_left.remove(d)
        t_left.remove(t)
    return pairs, d_left, t_left


def diff_intervals(
    db_rows: Sequence[Interval], truth: Sequence[Interval]
) -> MembershipPlan:
    """What must change so that the DB rows equal ``truth`` (dates decide)."""
    plan = MembershipPlan()
    db_by: dict[str, list[Interval]] = defaultdict(list)
    tr_by: dict[str, list[Interval]] = defaultdict(list)
    for r in db_rows:
        db_by[r.ticker].append(r)
    for t in truth:
        tr_by[t.ticker].append(t)

    for ticker in sorted(set(db_by) | set(tr_by)):
        pairs, del_left, ins_left = _pair_up(
            sorted(db_by.get(ticker, []), key=lambda r: (r.valid_from, r.id or 0)),
            sorted(tr_by.get(ticker, []), key=lambda r: r.valid_from),
        )
        for d, t in pairs:
            if (d.valid_from, d.valid_to) == (t.valid_from, t.valid_to):
                plan.unchanged += 1
            else:
                plan.updates.append(Update(old=d, new=t))
        plan.deletes.extend(del_left)
        plan.inserts.extend(ins_left)
    plan.inserts.sort(key=lambda i: (i.ticker, i.valid_from))
    plan.deletes.sort(key=lambda i: (i.ticker, i.valid_from))
    plan.updates.sort(key=lambda u: (u.old.ticker, u.old.valid_from))
    return plan


def format_plan(plan: MembershipPlan) -> list[str]:
    lines: list[str] = []
    for d in plan.deletes:
        lines.append(
            f"DELETE  {d.ticker:<7} {d.span():<26} [{d.source}] id={d.id} "
            "(not in the ground truth)"
        )
    for u in plan.updates:
        lines.append(
            f"UPDATE  {u.old.ticker:<7} {'; '.join(u.changes()):<52} "
            f"-> [{u.new.source}] {u.new.reason or ''}"
        )
    for i in plan.inserts:
        lines.append(
            f"INSERT  {i.ticker:<7} {i.span():<26} [{i.source}] {i.reason or ''}"
            + (f"  (successor {i.replaced_by})" if i.replaced_by else "")
        )
    return lines


# ── Validation: point-in-time member counts ──────────────────────────────


def merge_per_ticker(intervals: Iterable[Interval]) -> list[tuple[date, date]]:
    """Union of the intervals of each ticker (a ticker counts once per day)."""
    by_ticker: dict[str, list[tuple[date, date]]] = defaultdict(list)
    for iv in intervals:
        by_ticker[iv.ticker].append((iv.valid_from, iv.end))
    merged: list[tuple[date, date]] = []
    for spans in by_ticker.values():
        spans.sort()
        cur_from, cur_to = spans[0]
        for f, t in spans[1:]:
            if f <= cur_to:
                cur_to = max(cur_to, t)
            else:
                merged.append((cur_from, cur_to))
                cur_from, cur_to = f, t
        merged.append((cur_from, cur_to))
    return merged


def pit_counts(intervals: Iterable[Interval], days: Sequence[date]) -> list[int]:
    """Members per day: ``valid_from <= d`` and ``d < valid_to`` (open = member)."""
    diff = [0] * (len(days) + 1)
    for f, t in merge_per_ticker(intervals):
        i = bisect_left(days, f)
        j = bisect_left(days, t)
        if j > i:
            diff[i] += 1
            diff[j] -= 1
    counts, run = [], 0
    for k in range(len(days)):
        run += diff[k]
        counts.append(run)
    return counts


@dataclass
class PitReport:
    year_starts: list[tuple[date, int, int]]  # (session, before, after)
    window: tuple[date, date]
    before_min: tuple[int, date]
    before_max: tuple[int, date]
    after_min: tuple[int, date]
    after_max: tuple[int, date]
    outside_after: list[tuple[date, int]]
    outside_before: int
    sessions: int


def build_pit_report(
    before: Sequence[Interval],
    after: Sequence[Interval],
    sessions: Sequence[date],
    year_starts: Sequence[date],
    band: tuple[int, int] = PIT_BAND,
) -> PitReport:
    days = sorted(set(sessions) | set(year_starts))
    cb = dict(zip(days, pit_counts(before, days), strict=True))
    ca = dict(zip(days, pit_counts(after, days), strict=True))
    lo, hi = band

    def lowest(counts: dict[date, int]) -> tuple[int, date]:
        d = min(sessions, key=lambda x: (counts[x], x))
        return counts[d], d

    def highest(counts: dict[date, int]) -> tuple[int, date]:
        d = max(sessions, key=lambda x: (counts[x], -x.toordinal()))
        return counts[d], d

    return PitReport(
        year_starts=[(d, cb[d], ca[d]) for d in year_starts],
        window=(sessions[0], sessions[-1]),
        before_min=lowest(cb),
        before_max=highest(cb),
        after_min=lowest(ca),
        after_max=highest(ca),
        outside_after=[(d, ca[d]) for d in sessions if not lo <= ca[d] <= hi],
        outside_before=sum(1 for d in sessions if not lo <= cb[d] <= hi),
        sessions=len(sessions),
    )


def format_pit_report(
    rep: PitReport,
    band: tuple[int, int] = PIT_BAND,
    after_label: str = "after repair",
) -> list[str]:
    lines = [
        "Point-in-time S&P 500 members (tickers; first NYSE session of each year):"
    ]
    lines.append(f"  {'session':<12} {'DB now':>8} {after_label:>14}")
    for d, b, a in rep.year_starts:
        lines.append(f"  {d!s:<12} {b:>8} {a:>14}")
    lines.append(
        f"Over all {rep.sessions} sessions {rep.window[0]} .. {rep.window[1]}:"
    )
    lines.append(
        f"  DB now        min {rep.before_min[0]} ({rep.before_min[1]})  "
        f"max {rep.before_max[0]} ({rep.before_max[1]})  "
        f"outside {band[0]}-{band[1]}: {rep.outside_before} sessions"
    )
    lines.append(
        f"  {after_label:<13} min {rep.after_min[0]} ({rep.after_min[1]})  "
        f"max {rep.after_max[0]} ({rep.after_max[1]})  "
        f"outside {band[0]}-{band[1]}: {len(rep.outside_after)} sessions"
    )
    if rep.outside_after:
        shown = ", ".join(f"{d}={n}" for d, n in rep.outside_after[:20])
        more = len(rep.outside_after) - 20
        lines.append(
            f"  SESSIONS OUTSIDE {band[0]}-{band[1]} {after_label.upper()}: {shown}"
            + (f" (+{more} more)" if more > 0 else "")
        )
    return lines


def year_start_sessions(first_year: int, last_year: int) -> list[date]:
    from trading_signals.utils.market_calendar import trading_days

    result = []
    for year in range(first_year, last_year + 1):
        days = trading_days(date(year, 1, 1), date(year, 1, 12))
        if days:
            result.append(days[0])
    return result


def window_sessions() -> list[date]:
    """All NYSE sessions from ``data_start_date()`` to the last completed one."""
    from trading_signals.utils.market_calendar import (
        last_completed_session,
        trading_days,
    )
    from trading_signals.utils.retention import data_start_date

    return trading_days(data_start_date(), last_completed_session())


# ── Database ─────────────────────────────────────────────────────────────


def load_db_rows(session) -> list[Interval]:
    """All ``sp500`` rows of ``index_membership`` (other indexes are never read)."""
    from sqlalchemy import select

    from trading_signals.db.models.index_membership import IndexMembership

    stmt = (
        select(IndexMembership)
        .where(IndexMembership.index_name == INDEX_NAME)
        .order_by(IndexMembership.ticker, IndexMembership.valid_from)
    )
    return [
        Interval(
            ticker=r.ticker,
            valid_from=r.valid_from,
            valid_to=r.valid_to,
            reason=r.reason,
            replaced_by=r.replaced_by,
            source=r.source,
            id=r.id,
        )
        for r in session.execute(stmt).scalars().all()
    ]


def backup_rows(rows: Sequence[Interval]) -> list[dict]:
    return [
        {
            "id": r.id,
            "ticker": r.ticker,
            "index_name": INDEX_NAME,
            "valid_from": r.valid_from,
            "valid_to": r.valid_to,
            "reason": r.reason,
            "replaced_by": r.replaced_by,
            "source": r.source,
        }
        for r in rows
    ]


def apply_plan(session, plan: MembershipPlan, truth: Sequence[Interval]) -> None:
    """DELETE, UPDATE, INSERT (in that order; unique key ticker+index+valid_from).

    Every statement is restricted to ``index_name = 'sp500'``. After the writes
    the rows are re-read and compared with ``truth``; a mismatch raises (the
    caller's transaction is then rolled back).
    """
    from sqlalchemy import delete, insert, update

    from trading_signals.db.models.index_membership import IndexMembership as M

    if plan.deletes:
        session.execute(
            delete(M).where(
                M.index_name == INDEX_NAME, M.id.in_([r.id for r in plan.deletes])
            )
        )
    for u in plan.updates:
        session.execute(
            update(M)
            .where(M.index_name == INDEX_NAME, M.id == u.old.id)
            .values(
                valid_from=u.new.valid_from,
                valid_to=u.new.valid_to,
                reason=_clip(u.new.reason) or u.old.reason,
                replaced_by=u.new.replaced_by or u.old.replaced_by,
                source=u.new.source,
            )
        )
    if plan.inserts:
        session.execute(
            insert(M),
            [
                {
                    "ticker": i.ticker,
                    "index_name": INDEX_NAME,
                    "valid_from": i.valid_from,
                    "valid_to": i.valid_to,
                    "reason": _clip(i.reason) or "sp500 membership repair 2026-10",
                    "replaced_by": i.replaced_by,
                    "source": i.source,
                }
                for i in plan.inserts
            ],
        )
    session.flush()

    remaining = diff_intervals(load_db_rows(session), truth)
    if not remaining.empty:
        raise RuntimeError(
            "post-check failed: the table still differs from the ground truth "
            f"({len(remaining.inserts)} inserts, {len(remaining.updates)} updates, "
            f"{len(remaining.deletes)} deletes left) -- rolling back"
        )


# ── Orchestration ────────────────────────────────────────────────────────


def print_truth_summary(truth: GroundTruth) -> None:
    res = truth.result
    adds = sum(1 for c in res.changes if c.action == "added")
    rems = sum(1 for c in res.changes if c.action == "removed")
    open_n = sum(1 for iv in truth.intervals if iv.valid_to is None)
    by_source: dict[str, int] = defaultdict(int)
    for iv in truth.intervals:
        by_source[iv.source] += 1
    out("Ground truth")
    out(
        f"  change table: {len(res.changes)} events since {HISTORY_START} "
        f"({adds} additions, {rems} removals, newest event {res.as_of}), "
        f"{len(res.renames)} ticker renames in the table"
    )
    out(f"  current constituents: {len(truth.current)}")
    for event in truth.manual_applied:
        out(
            f"  manual event applied: {event.ticker} {event.action} "
            f"{event.change_date} ({event.reason})"
        )
    for event, why in truth.manual_skipped:
        out(f"  manual event skipped: {event.ticker} {event.action} -- {why}")
    for note in truth.notes:
        out(f"  note: {note}")
    out(
        f"  => {len(truth.intervals)} intervals ({open_n} open, "
        f"{len(truth.intervals) - open_n} closed); sources: "
        + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items()))
    )


def print_plan(plan: MembershipPlan, db_rows: Sequence[Interval]) -> None:
    out(f"Diff against signals.index_membership ({len(db_rows)} sp500 rows in the DB)")
    out(
        f"  unchanged {plan.unchanged}, INSERT {len(plan.inserts)}, "
        f"UPDATE {len(plan.updates)}, DELETE {len(plan.deletes)}"
    )
    for line in format_plan(plan):
        out("  " + line)


def pit_report_for(before: Sequence[Interval], after: Sequence[Interval]) -> PitReport:
    sessions = window_sessions()
    years = year_start_sessions(HISTORY_START.year, sessions[-1].year)
    return build_pit_report(before, after, sessions, years)


def run_rebuild(args: argparse.Namespace) -> int:
    truth = fetch_ground_truth()
    print_truth_summary(truth)
    if truth.problems:
        for p in truth.problems:
            out(f"  PROBLEM: {p}")
        raise AbortError(
            "the ground truth is not self-consistent (see PROBLEM lines); "
            "nothing written"
        )
    return _plan_and_apply(args, truth.intervals, restore=False)


def run_restore(args: argparse.Namespace) -> int:
    rows = read_csv_backup(args.restore)
    if not rows:
        raise AbortError(f"{args.restore} contains no rows")
    try:
        target = to_intervals(
            {
                "ticker": r["ticker"],
                "valid_from": date.fromisoformat(r["valid_from"]),
                "valid_to": date.fromisoformat(r["valid_to"])
                if r["valid_to"]
                else None,
                "reason": r.get("reason") or None,
                "replaced_by": r.get("replaced_by") or None,
                "source": r.get("source") or "wikipedia",
            }
            for r in rows
            if (r.get("index_name") or INDEX_NAME) == INDEX_NAME
        )
    except (KeyError, ValueError) as e:
        raise AbortError(f"{args.restore} is not a membership backup: {e}") from e
    out(f"Restoring the sp500 rows from {args.restore} ({len(target)} rows)")
    return _plan_and_apply(args, target, restore=True)


def _plan_and_apply(
    args: argparse.Namespace, target: list[Interval], restore: bool
) -> int:
    from sqlalchemy import text

    with repair_session(read_only=not args.apply) as session:
        if args.apply:
            # block concurrent writers (the monthly index_sync) until COMMIT
            session.execute(
                text("LOCK TABLE signals.index_membership IN SHARE ROW EXCLUSIVE MODE")
            )
        db_rows = load_db_rows(session)
        plan = diff_intervals(db_rows, target)
        print_plan(plan, db_rows)

        report = pit_report_for(db_rows, target)
        label = "after restore" if restore else "after repair"
        for line in format_pit_report(report, after_label=label):
            out(line)

        if not args.apply:
            out("")
            out("DRY RUN -- nothing written. Use --apply to write.")
            return 0

        if plan.empty:
            out(
                "Nothing to change -- the table already equals the target "
                "(no backup written)."
            )
            return 0
        if not restore:
            if len(plan.deletes) > args.max_deletes:
                raise AbortError(
                    f"{len(plan.deletes)} rows would be deleted (limit --max-deletes "
                    f"{args.max_deletes}); check the DELETE lines, then re-run with a "
                    "higher limit"
                )
            if report.outside_after and not args.ignore_validation:
                raise AbortError(
                    f"{len(report.outside_after)} sessions would have a member count "
                    f"outside {PIT_BAND[0]}-{PIT_BAND[1]}; fix the cause or pass "
                    "--ignore-validation"
                )

        try:
            path = write_csv_backup(
                backup_rows(db_rows), BACKUP_COLUMNS, args.backup_dir, BACKUP_PREFIX
            )
        except BackupError as e:
            raise AbortError(str(e)) from e
        out(f"Backup of {len(db_rows)} old sp500 rows written to {path}")

        apply_plan(session, plan, target)
        session.commit()
        out(
            f"APPLIED in one transaction: {len(plan.deletes)} deleted, "
            f"{len(plan.updates)} updated, {len(plan.inserts)} inserted; post-check OK."
        )
        out(f"Rollback: --restore {path} --apply")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--apply", action="store_true", help="Write to the DB (default: dry run)"
    )
    p.add_argument(
        "--backup-dir",
        default=DEFAULT_BACKUP_DIR,
        help="Directory for the CSV backup of the old sp500 rows (default: "
        "%(default)s)",
    )
    p.add_argument(
        "--max-deletes",
        type=int,
        default=DEFAULT_MAX_DELETES,
        help="Refuse --apply if more rows would be deleted (default: %(default)s)",
    )
    p.add_argument(
        "--ignore-validation",
        action="store_true",
        help=(
            "Apply even if sessions would have a member count outside "
            f"{PIT_BAND[0]}-{PIT_BAND[1]}"
        ),
    )
    p.add_argument(
        "--restore",
        metavar="BACKUP_CSV",
        default=None,
        help="Make the sp500 rows equal to a backup file written by an earlier --apply",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")
    announce_db_target("rebuild_sp500_membership")
    out(f"mode: {'APPLY' if args.apply else 'DRY RUN (read-only)'}")
    try:
        return run_restore(args) if args.restore else run_rebuild(args)
    except AbortError as e:
        out(f"ABORT: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
