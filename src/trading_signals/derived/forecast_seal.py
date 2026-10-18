"""Seal the forecast file before the open (``docs/FORECASTS.md`` section 2).

A forecast only counts for the pre-registered criterion if it existed before
the open (09:30 New York) of the session after the pack day. ``decided_at`` and
the file's modification time are chosen by whoever writes the file, so the
scheduler job ``forecast_seal`` records independent evidence: at 09:00, 09:15
and 09:25 New York on every NYSE session it computes the SHA-256 and size of
``context_packs/<previous session>/forecasts.yaml`` (the pack whose deadline is
the coming open) and stores them in ``signals.forecast_file_seals`` together
with the time of sealing. The ingest (``derived/forecasts.py``) marks a file
*on time* only if its hash was sealed before the open.

* :func:`seal_target` - the pack session and deadline that a seal run covers.
* :func:`seal_forecast_file` - one seal run (pure apart from the store and the
  file read); refuses to seal at or after the open.
* :class:`SqlSealStore` - the database side.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from trading_signals.derived.forecasts import FORECASTS_FILE, next_session_open
from trading_signals.utils.logging import get_logger
from trading_signals.utils.market_calendar import (
    NY_TZ,
    is_trading_day,
    previous_trading_day,
)

logger = get_logger(__name__)

#: Outcomes of a seal run.
SEALED = "sealed"  # new content recorded
UNCHANGED = "unchanged"  # this content was sealed before
NO_FILE = "no_file"  # no forecasts.yaml in the folder (yet)
NO_SESSION = "no_session"  # today is not an NYSE session
TOO_LATE = "too_late"  # at or after the open: nothing is sealed any more


class SealStore(Protocol):
    """What :func:`seal_forecast_file` needs from the database."""

    def add_seal(
        self,
        session_date: date,
        file_path: str,
        file_sha256: str,
        file_size: int,
        sealed_at: datetime,
    ) -> bool:
        """Insert a seal; ``False`` if this content is already sealed that day."""


@dataclass(frozen=True)
class SealResult:
    """Outcome of one seal run."""

    status: str
    session_date: date | None = None
    sha256: str | None = None
    size: int | None = None

    def notes(self) -> str:
        parts = [f"status={self.status}"]
        if self.session_date:
            parts.insert(0, f"session={self.session_date.isoformat()}")
        if self.sha256:
            parts.append(f"sha256={self.sha256[:12]}")
        if self.size is not None:
            parts.append(f"size={self.size}")
        return "; ".join(parts)


def seal_target(now: datetime) -> tuple[date, datetime] | None:
    """The pack session a seal run at ``now`` covers, and its deadline.

    ``None`` if the New York date of ``now`` is not an NYSE session. Otherwise
    the pack day is the session before it and the deadline is 09:30 New York of
    the current day - the open from which that pack's forecasts are evaluated
    (``next_session_open``).
    """
    today = now.astimezone(NY_TZ).date()
    if not is_trading_day(today):
        return None
    pack_day = previous_trading_day(today)
    return pack_day, next_session_open(pack_day)


def seal_forecast_file(
    store: SealStore, root: Path, now: datetime | None = None
) -> SealResult:
    """Seal ``forecasts.yaml`` of the pack that is due at the coming open.

    A run at or after the open seals nothing - a delayed job must never turn a
    late file into an on-time one. A missing file is no error (the pack may not
    have been assessed). The file is read as it is; a half-written file is
    sealed with its partial hash, which is harmless: only the hash of the final
    content counts, and it has to be sealed by a later run before the open.
    """
    now = now or datetime.now(UTC)
    target = seal_target(now)
    if target is None:
        return SealResult(NO_SESSION)
    pack_day, deadline = target
    if now >= deadline:
        logger.warning(
            f"[forecast_seal] {now.isoformat()} is not before the open "
            f"{deadline.isoformat()}: nothing sealed for {pack_day}"
        )
        return SealResult(TOO_LATE, pack_day)
    path = Path(root) / pack_day.isoformat() / FORECASTS_FILE
    if not path.is_file():
        return SealResult(NO_FILE, pack_day)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    added = store.add_seal(pack_day, str(path), sha, len(raw), now)
    logger.info(
        f"[forecast_seal] {pack_day}: {'sealed' if added else 'already sealed'} "
        f"{sha[:12]} ({len(raw)} bytes) at {now.astimezone(NY_TZ):%H:%M:%S} New York"
    )
    return SealResult(SEALED if added else UNCHANGED, pack_day, sha, len(raw))


class SqlSealStore:
    """:class:`SealStore` on a SQLAlchemy session (the caller commits)."""

    def __init__(self, session) -> None:
        self.session = session

    def add_seal(
        self,
        session_date: date,
        file_path: str,
        file_sha256: str,
        file_size: int,
        sealed_at: datetime,
    ) -> bool:
        from sqlalchemy.dialects.postgresql import insert

        from trading_signals.db.models.forecasts import ForecastFileSeal as M

        stmt = (
            insert(M)
            .values(
                session_date=session_date,
                file_path=file_path[:300],
                file_sha256=file_sha256,
                file_size=file_size,
                sealed_at=sealed_at,
            )
            # never overwrite: the FIRST sealing time of a content stands
            .on_conflict_do_nothing(
                constraint="uq_forecast_file_seals_session_date_file_sha256"
            )
        )
        return bool(self.session.execute(stmt).rowcount)


__all__ = [
    "NO_FILE",
    "NO_SESSION",
    "SEALED",
    "TOO_LATE",
    "UNCHANGED",
    "SealResult",
    "SealStore",
    "SqlSealStore",
    "seal_forecast_file",
    "seal_target",
]
