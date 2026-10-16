"""Shared helpers of the one-off data-repair scripts (``scripts/repair``).

* :func:`db_target` / :func:`announce_db_target` -- which database the process
  is about to touch (printed first by every repair script; the password is
  never shown).
* :func:`repair_session` -- a session that is physically read-only in dry-run
  mode (``SET TRANSACTION READ ONLY``), so a bug in the planning code cannot
  write anything.
* :func:`write_csv_backup` / :func:`read_csv_backup` -- a verified CSV dump to
  be written *before* a destructive step; the write is atomic (temp file +
  rename) and the row count is checked by re-reading the file.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any


class BackupError(RuntimeError):
    """The backup could not be written or did not verify -- do NOT go on."""


def out(message: str = "") -> None:
    """Report line on stdout (flushed, so it interleaves correctly with logs)."""
    print(message, flush=True)


def db_target() -> str:
    """``host:port/dbname (user ...)`` the settings resolve to (no password)."""
    from trading_signals.config import get_settings

    s = get_settings()
    return f"{s.DB_HOST}:{s.DB_PORT}/{s.DB_NAME} (user {s.DB_USER})"


def announce_db_target(script: str) -> str:
    """Print the resolved database target; call this before any DB access."""
    target = db_target()
    out(f"[{script}] database target: {target}")
    return target


@contextmanager
def repair_session(read_only: bool) -> Iterator[Any]:
    """Session for a repair step.

    ``read_only=True`` (dry run, planning): the transaction is declared
    ``READ ONLY`` before anything else, and is rolled back at the end.
    ``read_only=False``: the caller commits explicitly (``session.commit()``
    once per logical step); whatever is not committed is rolled back on exit.
    """
    from sqlalchemy import text

    from trading_signals.db.session import get_session_factory

    session = get_session_factory()()
    try:
        if read_only:
            session.execute(text("SET TRANSACTION READ ONLY"))
        yield session
    finally:
        try:
            session.rollback()
        finally:
            session.close()


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def write_csv_backup(
    rows: Iterable[Mapping[str, object]],
    columns: Sequence[str],
    directory: str | Path,
    prefix: str,
    now: datetime | None = None,
) -> Path:
    """Write ``rows`` to ``<directory>/<prefix>_<YYYYmmdd_HHMMSS>.csv`` and verify it.

    Dates are written as ISO strings, ``None`` as an empty cell. The file is
    written to a temp name first, re-read (row count must match) and only then
    renamed, so a half-written backup never carries the final name. Raises
    :class:`BackupError` on any problem; callers must stop before changing data.
    """
    data = list(rows)
    folder = Path(directory)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    path = folder / f"{prefix}_{stamp}.csv"
    n = 1
    while path.exists():
        n += 1
        path = folder / f"{prefix}_{stamp}_{n}.csv"
    tmp = path.with_name(path.name + ".tmp")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(columns)
            for row in data:
                writer.writerow([_cell(row.get(c)) for c in columns])
        with tmp.open("r", newline="", encoding="utf-8") as fh:
            written = sum(1 for _ in csv.reader(fh)) - 1
        if written != len(data):
            raise BackupError(
                f"backup verification failed: wrote {written} rows, "
                f"expected {len(data)}"
            )
        tmp.replace(path)
    except OSError as e:
        raise BackupError(
            f"cannot write the backup to {folder}: {type(e).__name__}: {e} "
            "(use --backup-dir with a writable directory)"
        ) from e
    except BackupError:
        tmp.unlink(missing_ok=True)
        raise
    return path


def read_csv_backup(path: str | Path) -> list[dict[str, str]]:
    """Rows of a backup written by :func:`write_csv_backup` (all values as text)."""
    try:
        with Path(path).open("r", newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError as e:
        raise BackupError(f"cannot read the backup {path}: {e}") from e
