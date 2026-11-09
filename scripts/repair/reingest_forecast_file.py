"""Add the forecast rows of a sealed ``forecasts.yaml`` that an old parser rejected.

**The bug (found 2026-11-09):** the parser read ``ticker: ON`` as the YAML 1.1
boolean ``True`` (ticker ``"TRUE"``), so the nightly ``forecast_review`` of
2026-11-04 stored the file of session 2026-11-02 as ``partial`` and dropped the
two ON rows ("TRUE hat am 2026-11-02 keinen Feature-Snapshot"). The parser is
fixed (``derived/forecasts.load_forecast_yaml``), but the regular review skips a
file whose SHA-256 it already knows (``forecast_reviews.file_sha256`` is
unique), so it never re-reads this file. This script does that one re-read.

**What it does** for ``--session YYYY-MM-DD``:

1. reads ``<CONTEXT_PACK_PATH>/<session>/forecasts.yaml``;
2. refuses unless the SHA-256 of the file equals a stored seal
   (``forecast_file_seals``) of that session - only the content that was
   sealed before the open may count - and a stored review of exactly this hash
   exists (otherwise the nightly review will simply read the file);
3. parses it with the fixed parser and computes ``on_time`` exactly like the
   regular ingest (``decided_at`` and the seal time before the open); a
   difference from the stored review's flag refuses the repair;
4. inserts only the rows that are valid now, have a feature snapshot on the
   session day and are not stored yet for that review (key: review, ticker,
   horizon) - existing rows are never touched. The rows belong to the EXISTING
   review (a new review is impossible: the hash is unique), which keeps the
   evaluation's "latest review per session" rule intact;
5. recomputes ``n_forecasts``, ``n_rejected``, ``status`` and ``errors`` of
   that review from the same parse, so the report no longer lists the lost rows.

Idempotent: a second run finds nothing to add. **Dry run (default):** read-only
session (``SET TRANSACTION READ ONLY`` and ``default_transaction_read_only =
on``), prints per row what would be added. **``--apply``:** planning and write in
ONE transaction. Needs Sebastian's OK on production (there are no backups); do
not run it between 14:55 and 15:35 Berlin time or 22:15-05:15 (``alpaca-ops``
refuses anyway).

Usage (inside the container, ``alpaca-ops run``)::

    scripts/repair/reingest_forecast_file.py --session 2026-11-02            # dry run
    scripts/repair/reingest_forecast_file.py --session 2026-11-02 --apply
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from trading_signals.derived.forecasts import (
    FORECASTS_FILE,
    MAX_STORED_ERRORS,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_REJECTED,
    ForecastFileError,
    ForecastRecord,
    RejectedRow,
    _check_tickers,
    _message,
    forecast_records,
    is_on_time,
    next_session_open,
    parse_forecasts,
)
from trading_signals.utils.logging import setup_logging
from trading_signals.utils.repair_support import announce_db_target, out, repair_session

SCRIPT = "reingest_forecast_file"


class RepairRefusedError(Exception):
    """A safety check failed; nothing is written."""


@dataclass(frozen=True)
class StoredReview:
    """The stored review of the file (row of ``forecast_reviews``)."""

    id: int
    session_date: date
    file_sha256: str
    on_time: bool
    status: str
    n_forecasts: int
    n_rejected: int


@dataclass
class RepairPlan:
    review: StoredReview
    on_time: bool
    to_add: list[ForecastRecord] = field(default_factory=list)
    already_stored: list[tuple[str, int]] = field(default_factory=list)
    still_rejected: list[RejectedRow] = field(default_factory=list)
    #: New values of the review row (after the repair).
    n_forecasts: int = 0
    n_rejected: int = 0
    status: str = STATUS_OK
    errors: list[str] = field(default_factory=list)


def build_plan(
    raw: bytes,
    folder_date: date,
    sealed: dict[str, datetime],
    review: StoredReview | None,
    stored: set[tuple[str, int]],
    known_tickers: set[str],
    open_fn: Callable[[date], datetime] = next_session_open,
) -> RepairPlan:
    """Decide what to add (pure; every refusal raises :class:`RepairRefusedError`).

    Args:
        raw: Bytes of the file as it lies in the pack folder now.
        folder_date: Session of the pack folder.
        sealed: ``{sha256: first sealed_at}`` of the session's seals.
        review: The stored review with exactly this hash (``None``: none).
        stored: ``(ticker, horizon_days)`` already stored for that review.
        known_tickers: Tickers with a feature snapshot on the session day.
    """
    sha = hashlib.sha256(raw).hexdigest()
    if sha not in sealed:
        raise RepairRefusedError(
            f"SHA-256 {sha[:12]} of the file is not a stored seal of {folder_date} "
            "(the file changed after the last seal run): refused"
        )
    if review is None or review.file_sha256 != sha:
        raise RepairRefusedError(
            f"no stored review with hash {sha[:12]}: the nightly forecast_review "
            "has not read this file yet and will read it with the fixed parser"
        )
    if review.session_date != folder_date:
        raise RepairRefusedError(
            f"the review belongs to {review.session_date}, not to {folder_date}"
        )
    try:
        parsed = parse_forecasts(raw.decode("utf-8-sig"), folder_date)
    except (ForecastFileError, UnicodeDecodeError) as e:
        raise RepairRefusedError(f"the file is invalid: {e}") from e
    if not known_tickers:
        raise RepairRefusedError(f"no feature snapshot on {folder_date}")

    entry_open = open_fn(folder_date)
    on_time = is_on_time(parsed.decided_at, sealed[sha], entry_open)
    if on_time != review.on_time:
        raise RepairRefusedError(
            f"on_time would be {on_time}, the stored review says {review.on_time}: "
            "inconsistent seal data, refused"
        )

    kept, unknown = _check_tickers(parsed, known_tickers)
    rejected = sorted([*parsed.rejected, *unknown], key=lambda r: r.index)
    fresh = [f for f in kept if (f.ticker, f.horizon_days) not in stored]
    plan = RepairPlan(
        review=review,
        on_time=on_time,
        to_add=forecast_records(fresh, parsed, entry_open, on_time),
        already_stored=[
            (f.ticker, f.horizon_days)
            for f in kept
            if (f.ticker, f.horizon_days) in stored
        ],
        still_rejected=rejected,
        n_forecasts=len(kept),
        n_rejected=len(rejected),
        status=(
            STATUS_OK if not rejected else (STATUS_PARTIAL if kept else STATUS_REJECTED)
        ),
        errors=[_message(r.message()) for r in rejected][:MAX_STORED_ERRORS],
    )
    return plan


def summarize(plan: RepairPlan, folder_date: date) -> list[str]:
    """Report lines: per row what is added, what stays, what the review becomes."""
    r = plan.review
    lines = [
        f"session {folder_date}: review {r.id} ({r.status}, {r.n_forecasts} stored, "
        f"{r.n_rejected} rejected, on_time={r.on_time})",
        f"already stored (untouched): {len(plan.already_stored)}",
        f"to add: {len(plan.to_add)}",
    ]
    for f in plan.to_add:
        lines.append(
            f"  + {f.ticker} {f.horizon_days}d {f.direction} "
            f"conf={f.confidence} conv={f.conviction} on_time={f.on_time}"
        )
    lines.append(f"rows that stay rejected: {len(plan.still_rejected)}")
    for row in plan.still_rejected:
        lines.append(f"  - {row.message()}")
    lines.append(
        f"review afterwards: {plan.status}, {plan.n_forecasts} forecasts, "
        f"{plan.n_rejected} rejected"
    )
    return lines


def load_inputs(session, folder_date: date, sha: str):
    """``(sealed, review, stored, known_tickers)`` from the database."""
    from sqlalchemy import select

    from trading_signals.db.models.forecasts import Forecast, ForecastReview
    from trading_signals.derived.forecasts import SqlForecastStore

    store = SqlForecastStore(session)
    row = session.execute(
        select(ForecastReview).where(ForecastReview.file_sha256 == sha)
    ).scalar_one_or_none()
    review = (
        None
        if row is None
        else StoredReview(
            row.id,
            row.session_date,
            row.file_sha256,
            row.on_time,
            row.status,
            row.n_forecasts,
            row.n_rejected,
        )
    )
    stored: set[tuple[str, int]] = set()
    if row is not None:
        stored = {
            (t, h)
            for t, h in session.execute(
                select(Forecast.ticker, Forecast.horizon_days).where(
                    Forecast.review_id == row.id
                )
            ).all()
        }
    return (
        store.seal_times(folder_date),
        review,
        stored,
        store.session_tickers(folder_date),
    )


def apply_plan(session, plan: RepairPlan) -> int:
    """Insert the new rows and update the review (the caller commits)."""
    from sqlalchemy import update

    from trading_signals.db.models.forecasts import Forecast, ForecastReview

    for f in plan.to_add:
        session.add(
            Forecast(
                review_id=plan.review.id,
                session_date=f.session_date,
                ticker=f.ticker,
                horizon_days=f.horizon_days,
                direction=f.direction,
                confidence=f.confidence,
                conviction=f.conviction,
                expected_pct=f.expected_pct,
                drivers=f.drivers or None,
                reason=f.reason,
                risks=f.risks,
                forecaster_version=f.forecaster_version,
                decided_at=f.decided_at,
                valid_from=f.valid_from,
                on_time=f.on_time,
            )
        )
    session.flush()
    session.execute(
        update(ForecastReview)
        .where(ForecastReview.id == plan.review.id)
        .values(
            n_forecasts=plan.n_forecasts,
            n_rejected=plan.n_rejected,
            status=plan.status,
            errors=plan.errors or None,
        )
    )
    return len(plan.to_add)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--session", required=True, help="pack day, YYYY-MM-DD")
    p.add_argument("--apply", action="store_true", help="write (default: dry run)")
    p.add_argument(
        "--root", help="context pack root (default: settings CONTEXT_PACK_PATH)"
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")
    announce_db_target(SCRIPT)
    from sqlalchemy import text

    from trading_signals.config import get_settings

    folder_date = date.fromisoformat(args.session)
    root = Path(args.root or get_settings().CONTEXT_PACK_PATH)
    path = root / folder_date.isoformat() / FORECASTS_FILE
    out(f"[{SCRIPT}] file: {path}, mode: {'APPLY' if args.apply else 'dry run'}")
    try:
        raw = path.read_bytes()
    except OSError as e:
        out(f"REFUSED: cannot read the file: {e}")
        return 1
    sha = hashlib.sha256(raw).hexdigest()
    out(f"[{SCRIPT}] sha256: {sha}")

    try:
        with repair_session(read_only=not args.apply) as session:
            if not args.apply:
                session.execute(text("SET default_transaction_read_only = on"))
            sealed, review, stored, known = load_inputs(session, folder_date, sha)
            plan = build_plan(raw, folder_date, sealed, review, stored, known)
            for line in summarize(plan, folder_date):
                out(line)
            if not args.apply:
                out(f"dry run done: nothing written; --apply would add "
                    f"{len(plan.to_add)} rows")
                return 0
            added = apply_plan(session, plan)
            session.commit()
            out(f"applied: {added} rows added to review {plan.review.id}")
            if added != len(plan.to_add):
                out("WARNING: the number of added rows differs from the plan")
    except RepairRefusedError as e:
        out(f"REFUSED: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
