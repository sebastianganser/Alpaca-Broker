"""Shared DB helpers for collectors (bulk upsert, active-ticker lookup).

Kept in a separate module (not ``base.py``) so that helpers such as
``gap_detector`` or ``universe.onboarder`` can use them without import
cycles.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

DEFAULT_CHUNK = 1000


def bulk_insert(
    session: Session,
    model: Any,
    rows: Iterable[dict],
    conflict_cols: Sequence[str] | None = None,
    *,
    constraint: str | None = None,
    index_where: Any = None,
    update_cols: Sequence[str] | None = None,
    update_where: Any = None,
    chunk: int = DEFAULT_CHUNK,
) -> int:
    """Insert ``rows`` in multi-row INSERT statements (``chunk`` rows each).

    Args:
        session: Active session (not committed here).
        model: ORM model class.
        rows: Iterable of column dicts. Missing keys are filled with ``None``
            so that all rows of a statement have identical columns.
        conflict_cols: Columns of the unique index for ON CONFLICT. If
            neither this nor ``constraint`` is given, a plain INSERT is used.
        constraint: Name of a unique constraint (alternative to
            ``conflict_cols``).
        index_where: WHERE clause of a *partial* unique index.
        update_cols: If given → ``ON CONFLICT DO UPDATE SET col = excluded.col``
            for these columns; otherwise ``DO NOTHING``.
        update_where: Optional WHERE for the DO UPDATE (e.g. only replace
            extrapolated rows).
        chunk: Rows per statement.

    Returns:
        Number of rows inserted/updated as reported by the driver.

    Rows with duplicate conflict keys inside one call are de-duplicated
    (last one wins) – PostgreSQL rejects DO UPDATE statements that touch the
    same row twice.
    """
    rows = [dict(r) for r in rows]
    if not rows:
        return 0

    if conflict_cols:
        dedup: dict[tuple, dict] = {}
        for r in rows:
            dedup[tuple(r.get(c) for c in conflict_cols)] = r
        rows = list(dedup.values())

    all_keys: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                all_keys.append(k)
    rows = [{k: r.get(k) for k in all_keys} for r in rows]

    written = 0
    for i in range(0, len(rows), max(1, chunk)):
        part = rows[i : i + chunk]
        stmt = pg_insert(model).values(part)
        if conflict_cols or constraint:
            target: dict[str, Any] = (
                {"constraint": constraint}
                if constraint
                else {"index_elements": list(conflict_cols or [])}
            )
            if index_where is not None and not constraint:
                target["index_where"] = index_where
            if update_cols:
                set_ = {c: stmt.excluded[c] for c in update_cols}
                stmt = stmt.on_conflict_do_update(
                    **target, set_=set_, where=update_where
                )
            else:
                stmt = stmt.on_conflict_do_nothing(**target)
        result = session.execute(stmt)
        rc = getattr(result, "rowcount", 0)
        if isinstance(rc, int) and rc > 0:
            written += rc
    return written


def active_tickers(session: Session, release: bool = True) -> list[str]:
    """Return all active universe tickers (sorted).

    If ``release`` is True the read transaction is committed right away so
    that no connection/transaction is held open during the long HTTP phase
    that typically follows (``expire_on_commit=False`` → no side effects).
    """
    from trading_signals.db.models.universe import Universe

    stmt = (
        select(Universe.ticker)
        .where(Universe.is_active.is_(True))
        .order_by(Universe.ticker)
    )
    tickers = [row[0] for row in session.execute(stmt).all()]
    if release:
        release_transaction(session)
    return tickers


def release_transaction(session: Session) -> None:
    """End the current (read) transaction so no connection idles in a txn."""
    try:
        session.commit()
    except Exception:  # pragma: no cover - never break a run for this
        session.rollback()
