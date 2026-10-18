"""Attribute stored multi-party SEC filings (13D / 13G / 144) to their subject company.

**The bug (found 2026-10-18 on production data):** EDGAR lists a Schedule 13D/13G
or a Form 144 in the submissions of EVERY party - the filer / holder (BlackRock,
JPMorgan, State Street ... filing a 13G about another company) and the subject
company (the issuer the stake or sale is about). ``SecFilingsCollector`` stored
whichever copy it met first, so ``signals.sec_filings`` holds e.g. 650 BLK and
162 STT 13G rows that are stakes in OTHER companies, JPM rows whose URL points to
a 13G about someone else, and about 200 subject copies were dropped by the unique
accession number. The collector is fixed (it stores a multi-party filing only
under the ``SUBJECT COMPANY`` of its SEC header); this script repairs the rows
that are already stored.

**What it does:** for every stored row of a multi-party form (``144``, ``SC 13D``,
``SC 13G``, ``SCHEDULE 13D`` / ``13G``, each with ``/A``) it reads the SEC header
of the filing (``SECClient.fetch_filing_header``, one small request per row, the
client's 9 requests/s rate limit and User-Agent) and decides, with the same
rules as the collector (``classify_row``):

* **keep** - the stored company is the subject (a self-filed 144 of an issuer,
  or a copy that was already stored under the subject);
* **update** - the subject is ANOTHER active universe company: ``cik``,
  ``ticker`` (first alphabetical), ``tickers`` (all active tickers of that CIK)
  and ``url`` are set to the subject's (the accession number, form, dates and
  document stay);
* **delete** - the subject is not an active universe company (or, for a header
  without a ``SUBJECT COMPANY`` block, the stored company is a named filer).
  Multi-party forms have no ``sec_filing_texts`` rows, so nothing cascades.

A row whose header cannot be read is **left untouched** and listed as an error
(exit code 1); simply run the script again. With several subject blocks the
first one that is an active universe company wins.

**Dry run (default):** read-only database session (``SET TRANSACTION READ ONLY``
and ``default_transaction_read_only = on``), SEC GET requests only; prints the
number of rows per action and form and 10 examples per action. Nothing is
written. **``--apply``:** the same planning, then ONE transaction with all
updates and deletes; idempotent (a second run keeps everything). Needs
Sebastian's OK on production; there are no database backups, so read the dry run.

Run time: one header request per stored multi-party row, ~2,000 13D/13G rows +
~3,500 Form 144 rows on production (120-day backfill) = ~5,500 requests at
~0.2-0.25 s = **roughly 20-25 minutes** for the dry run and again for ``--apply``.
Run it detached on Unraid (``alpaca-ops run-bg``), outside 22:15-05:15 and not at
04:00 (``sec_filings_collector``), Sunday 14:00 (``sec_financials_collector``)
or during the seal runs. The database is only touched at the start (read) and,
with ``--apply``, at the end (one short write transaction); no connection idles
during the SEC requests.

Usage (inside the container / with .env configured)::

    PY=.venv/bin/python
    $PY scripts/repair/fix_sec_filing_subjects.py                   # dry run
    $PY scripts/repair/fix_sec_filing_subjects.py --apply
    $PY scripts/repair/fix_sec_filing_subjects.py --tickers BLK,JPM   # rows under these

Verify afterwards: the dry run again (everything ``keep``), and
``select ticker, count(*) from signals.sec_filings where form like 'SCHEDULE 13%'
or form like 'SC 13%' group by 1 order by 2 desc`` no longer shows BLK / STT / JPM
at the top.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from trading_signals.collectors.sec_client import HeaderParties, SECClient
from trading_signals.collectors.sec_filings_collector import (
    MULTI_PARTY_FORMS,
    filing_url,
    group_by_cik,
    header_parties,
)
from trading_signals.utils.logging import setup_logging
from trading_signals.utils.repair_support import (
    announce_db_target,
    out,
    repair_session,
)

#: Seconds per header request used for the time estimate (rate limit + transfer).
SECONDS_PER_REQUEST = 0.25

#: Examples printed per action in the report.
EXAMPLES = 10

#: Progress line every N rows.
PROGRESS_EVERY = 250

KEEP, UPDATE, DELETE = "keep", "update", "delete"
ACTIONS = (KEEP, UPDATE, DELETE)


@dataclass(frozen=True)
class Row:
    """The columns of a stored ``sec_filings`` row the repair needs."""

    id: int
    accession_number: str
    form: str
    cik: str
    ticker: str
    filing_date: date
    primary_document: str | None


@dataclass(frozen=True)
class Decision:
    """What to do with one stored row."""

    action: str  # keep | update | delete
    row: Row
    reason: str
    new_cik: str | None = None
    new_tickers: tuple[str, ...] = ()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="write (default: dry run)")
    p.add_argument(
        "--tickers",
        help="comma separated tickers: only rows currently stored under one of them",
    )
    return p.parse_args(argv)


# ── Pure logic (unit-tested) ─────────────────────────────────────────────


def classify_row(
    row: Row, parties: HeaderParties, groups: dict[str, list[str]]
) -> Decision:
    """Keep, re-attribute or delete one stored multi-party row.

    ``parties`` are the parties of the filing's SEC header, ``groups`` maps the
    CIK of every active universe company to its sorted active tickers (the same
    grouping as the collector's). Same rules as ``SecFilingsCollector``: the
    filing belongs to the ``SUBJECT COMPANY``; without such a block the stored
    company stays only if the header does not name it as filer / owner.
    """
    if parties.subjects:
        if row.cik in parties.subjects:
            return Decision(KEEP, row, "the stored company is the subject")
        for subject in parties.subjects:
            if subject in groups:
                return Decision(
                    UPDATE,
                    row,
                    f"subject is {subject} ({', '.join(groups[subject])}), "
                    f"the stored company is not",
                    new_cik=subject,
                    new_tickers=tuple(groups[subject]),
                )
        subjects = ", ".join(parties.subjects)
        return Decision(
            DELETE, row, f"subject ({subjects}) is not an active universe company"
        )
    if parties.is_subject(row.cik):
        return Decision(KEEP, row, "no subject block; stored company is no named filer")
    return Decision(DELETE, row, "no subject block; stored company is a named filer")


def plan(
    rows: Iterable[Row],
    groups: dict[str, list[str]],
    fetch_parties: Callable[[str, str], HeaderParties],
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[Decision], list[tuple[Row, str]]]:
    """Decide every row; returns ``(decisions, errors)``.

    ``fetch_parties(cik, accession)`` reads one header. A row whose header
    cannot be read lands in ``errors`` (``(row, message)``) and gets no decision.
    """
    rows = list(rows)
    decisions: list[Decision] = []
    errors: list[tuple[Row, str]] = []
    for i, row in enumerate(rows, 1):
        try:
            parties = fetch_parties(row.cik, row.accession_number)
        except Exception as e:  # one bad header must not stop the run
            errors.append((row, f"{type(e).__name__}: {e}"))
        else:
            decisions.append(classify_row(row, parties, groups))
        if progress is not None:
            progress(i, len(rows))
    return decisions, errors


def _example(d: Decision) -> str:
    row = d.row
    target = (
        f" -> {d.new_cik} ({', '.join(d.new_tickers)})" if d.action == UPDATE else ""
    )
    return (
        f"  {row.accession_number} {row.form:<15} {row.filing_date} "
        f"{row.ticker:<6}{target}  [{d.reason}]"
    )


def summarize(
    decisions: Sequence[Decision], errors: Sequence[tuple[Row, str]]
) -> list[str]:
    """Human-readable report: counts per action and form, examples per action."""
    by_action = Counter(d.action for d in decisions)
    lines = [
        f"rows checked: {len(decisions) + len(errors)} "
        f"(keep {by_action[KEEP]}, update {by_action[UPDATE]}, "
        f"delete {by_action[DELETE]}, header errors {len(errors)})"
    ]
    forms = sorted({d.row.form for d in decisions})
    if forms:
        per_form = Counter((d.row.form, d.action) for d in decisions)
        lines.append(f"by form:   {'form':<16}{'keep':>8}{'update':>8}{'delete':>8}")
        for form in forms:
            counts = "".join(f"{per_form[(form, a)]:>8}" for a in ACTIONS)
            lines.append(f"           {form:<16}{counts}")
    by_ticker = Counter(d.row.ticker for d in decisions if d.action != KEEP)
    if by_ticker:
        top = ", ".join(f"{t} {n}" for t, n in by_ticker.most_common(10))
        lines.append(f"most affected tickers (rows to change): {top}")
    for action in (UPDATE, DELETE, KEEP):
        sample = [d for d in decisions if d.action == action][:EXAMPLES]
        if sample:
            lines.append(f"examples - {action} ({by_action[action]} rows):")
            lines += [_example(d) for d in sample]
    if errors:
        lines.append(f"header errors ({len(errors)}; the rows stay untouched):")
        lines += [
            f"  {r.accession_number} {r.form:<15} {r.ticker:<6} {msg}"
            for r, msg in errors[:EXAMPLES]
        ]
    return lines


# ── Database access (thin, mocked in tests) ──────────────────────────────


def load_rows(session, tickers: list[str] | None) -> list[Row]:
    """Stored rows of a multi-party form (optionally only under ``tickers``)."""
    from sqlalchemy import select

    from trading_signals.db.models.sec import SecFiling as F

    stmt = (
        select(
            F.id,
            F.accession_number,
            F.form,
            F.cik,
            F.ticker,
            F.filing_date,
            F.primary_document,
        )
        .where(F.form.in_(sorted(MULTI_PARTY_FORMS)))
        .order_by(F.filing_date, F.id)
    )
    if tickers:
        # varchar(20)[] && text[] needs both sides cast (see context_pack.loader)
        from sqlalchemy import Text, cast
        from sqlalchemy.dialects.postgresql import ARRAY, array

        stmt = stmt.where(
            cast(F.tickers, ARRAY(Text)).overlap(array(list(tickers), type_=Text))
        )
    return [Row(*r) for r in session.execute(stmt).all()]


def apply_decisions(session, decisions: Iterable[Decision]) -> dict[str, int]:
    """Write the updates and deletes in the caller's transaction (no commit).

    Returns the number of rows changed per action; a count that differs from
    the plan means the table changed meanwhile and is reported by the caller.
    """
    from sqlalchemy import delete, update

    from trading_signals.db.models.sec import SecFiling as F

    decisions = list(decisions)
    updated = 0
    for d in decisions:
        if d.action != UPDATE:
            continue
        result = session.execute(
            update(F)
            .where(F.id == d.row.id)
            .values(
                cik=d.new_cik,
                ticker=d.new_tickers[0],
                tickers=list(d.new_tickers),
                url=filing_url(
                    d.new_cik, d.row.accession_number, d.row.primary_document
                ),
            )
        )
        updated += max(result.rowcount or 0, 0)
    delete_ids = [d.row.id for d in decisions if d.action == DELETE]
    deleted = 0
    for i in range(0, len(delete_ids), 500):
        result = session.execute(
            delete(F).where(F.id.in_(delete_ids[i : i + 500]))
        )
        deleted += max(result.rowcount or 0, 0)
    return {UPDATE: updated, DELETE: deleted}


# ── Main ─────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")
    announce_db_target("fix_sec_filing_subjects")

    from sqlalchemy import text

    from trading_signals.collectors._db import active_tickers

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else None
    )
    out(f"[fix_sec_filing_subjects] forms: {', '.join(sorted(MULTI_PARTY_FORMS))}")
    out(f"[fix_sec_filing_subjects] rows under tickers: "
        f"{', '.join(tickers) if tickers else 'all'}, "
        f"mode: {'APPLY' if args.apply else 'dry run'}")

    # 1. read phase: the rows and the active universe (read-only session)
    with repair_session(read_only=True) as session:
        session.execute(text("SET default_transaction_read_only = on"))
        universe = active_tickers(session, release=False)
        rows = load_rows(session, tickers)
    out(f"stored multi-party rows: {len(rows)}; active universe tickers: "
        f"{len(universe)}")
    if not rows:
        out("nothing to do")
        return 0
    out(f"estimated run time: ~{len(rows) * SECONDS_PER_REQUEST / 60:.0f} minutes "
        f"({len(rows)} header requests)")

    # 2. network phase (no database connection is held)
    client = SECClient()
    client.load_cik_mapping()
    groups, no_cik = group_by_cik(client, universe)
    out(f"universe companies with a CIK: {len(groups)} ({no_cik} tickers without)")
    started = time.monotonic()

    def progress(done: int, total: int) -> None:
        if done % PROGRESS_EVERY == 0 or done == total:
            out(f"  {done}/{total} headers read ({time.monotonic() - started:.0f} s)")

    decisions, errors = plan(
        rows,
        groups,
        lambda cik, accession: header_parties(client, cik, accession),
        progress,
    )
    for line in summarize(decisions, errors):
        out(line)

    # 3. write phase (apply only): one transaction
    changes = [d for d in decisions if d.action != KEEP]
    if not args.apply:
        out(f"dry run done: nothing written; --apply would update "
            f"{sum(d.action == UPDATE for d in decisions)} and delete "
            f"{sum(d.action == DELETE for d in decisions)} rows")
    elif changes:
        with repair_session(read_only=False) as session:
            done = apply_decisions(session, changes)
            session.commit()
        want = Counter(d.action for d in changes)
        out(f"applied: updated {done[UPDATE]}/{want[UPDATE]}, "
            f"deleted {done[DELETE]}/{want[DELETE]} rows")
        if done[UPDATE] != want[UPDATE] or done[DELETE] != want[DELETE]:
            out("WARNING: the changed row counts differ from the plan "
                "(the table changed meanwhile) - run the dry run again")
    else:
        out("nothing to change")
    if errors:
        out(f"{len(errors)} header lookups failed - their rows were not "
            f"touched; run the script again")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
