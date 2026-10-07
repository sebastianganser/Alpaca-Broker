"""Stage-2 decisions: read ``decisions.yaml`` from the context packs (concept §7.7).

The Claude broker skill reviews a context pack and writes
``context_packs/<YYYY-MM-DD>/decisions.yaml`` (format: ``docs/STAGE2_DECISIONS.md``).
The nightly step ``stage2_review`` reads these files, validates them and
stores them in ``signals.stage2_reviews`` / ``signals.stage2_decisions``.

* :func:`parse_decisions` – validate one file (pure, raises
  :class:`DecisionFileError` with a German message).
* :func:`next_session_open` / :func:`is_on_time` – a decision counts for the
  forward test only if it was made (and the file last written) before the
  open of the session after the pack day – the trade enters at that open.
* :func:`ingest_decisions` – scan the pack folders of the last
  :data:`LOOKBACK_DAYS` days; changed files replace the stored day.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import yaml
from sqlalchemy import delete
from sqlalchemy.orm import Session

from trading_signals.db.base import APP_TIMEZONE
from trading_signals.db.models.stage2 import Stage2Decision, Stage2Review
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

SCHEMA_ID = "stage2-decisions/v1"
DECISIONS_FILE = "decisions.yaml"
ACTIONS = ("buy", "no_entry")
LOOKBACK_DAYS = 45
MAX_REASON_LEN = 1000

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_PACK_FILE_RE = re.compile(r"^(\d{2})_([A-Z0-9.\-]+)\.md$")


class DecisionFileError(ValueError):
    """Invalid ``decisions.yaml`` (message is shown in the job log)."""


@dataclass(frozen=True)
class ParsedDecision:
    ticker: str
    action: str
    reason: str | None = None
    limit_price: float | None = None
    target_pct: float | None = None
    stop_pct: float | None = None


@dataclass(frozen=True)
class ParsedReview:
    session_date: date
    decided_at: datetime  # timezone-aware
    decisions: list[ParsedDecision]


@dataclass
class IngestResult:
    files: int = 0
    ingested: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)

    def notes(self) -> str:
        text = (
            f"files={self.files}, ingested={self.ingested}, "
            f"unchanged={self.unchanged}, errors={len(self.errors)}"
        )
        if self.errors:
            text += "; " + "; ".join(self.errors[:5])
        return text


def _as_date(val, what: str) -> date:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val.strip())
        except ValueError:
            pass
    raise DecisionFileError(f"{what}: kein Datum (JJJJ-MM-TT): {val!r}")


def _as_datetime(val, what: str) -> datetime:
    if isinstance(val, str):
        try:
            val = datetime.fromisoformat(val.strip())
        except ValueError as e:
            raise DecisionFileError(f"{what}: kein Zeitpunkt (ISO 8601): {val!r}") from e
    if not isinstance(val, datetime):
        raise DecisionFileError(f"{what}: kein Zeitpunkt (ISO 8601): {val!r}")
    # without a zone the skill means German wall-clock time
    return val if val.tzinfo else val.replace(tzinfo=APP_TIMEZONE)


def _as_float(val, what: str) -> float | None:
    if val is None:
        return None
    if isinstance(val, bool):
        raise DecisionFileError(f"{what}: keine Zahl: {val!r}")
    try:
        f = float(val)
    except (TypeError, ValueError) as e:
        raise DecisionFileError(f"{what}: keine Zahl: {val!r}") from e
    if not math.isfinite(f):
        raise DecisionFileError(f"{what}: keine Zahl: {val!r}")
    return f


def parse_decisions(text: str, folder_date: date) -> ParsedReview:
    """Validate the content of one ``decisions.yaml`` (pure).

    Args:
        text: File content.
        folder_date: Date of the pack folder; ``session`` must match it.

    Raises:
        DecisionFileError: Format violation (German message).
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise DecisionFileError(f"kein gültiges YAML: {e}") from e
    if not isinstance(doc, dict):
        raise DecisionFileError("Datei muss ein YAML-Objekt sein")
    if doc.get("schema") != SCHEMA_ID:
        raise DecisionFileError(f"schema muss '{SCHEMA_ID}' sein, ist {doc.get('schema')!r}")
    if "session" not in doc:
        raise DecisionFileError("Feld 'session' fehlt")
    session_date = _as_date(doc["session"], "session")
    if session_date != folder_date:
        raise DecisionFileError(
            f"session {session_date} passt nicht zum Ordner {folder_date}"
        )
    if "decided_at" not in doc:
        raise DecisionFileError("Feld 'decided_at' fehlt")
    decided_at = _as_datetime(doc["decided_at"], "decided_at")
    raw = doc.get("decisions")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise DecisionFileError("'decisions' muss eine Liste sein")

    decisions: list[ParsedDecision] = []
    seen: set[str] = set()
    for i, item in enumerate(raw, 1):
        where = f"decisions[{i}]"
        if not isinstance(item, dict):
            raise DecisionFileError(f"{where}: Eintrag muss ein Objekt sein")
        ticker = str(item.get("ticker") or "").strip().upper()
        if not _TICKER_RE.match(ticker):
            raise DecisionFileError(f"{where}: ungültiger Ticker {item.get('ticker')!r}")
        if ticker in seen:
            raise DecisionFileError(f"{where}: Ticker {ticker} doppelt")
        seen.add(ticker)
        action = str(item.get("action") or "").strip().lower()
        if action not in ACTIONS:
            raise DecisionFileError(
                f"{where} ({ticker}): action muss buy oder no_entry sein, ist "
                f"{item.get('action')!r}"
            )
        reason = item.get("reason")
        reason = str(reason).strip()[:MAX_REASON_LEN] if reason is not None else None
        decisions.append(ParsedDecision(
            ticker=ticker,
            action=action,
            reason=reason or None,
            limit_price=_as_float(item.get("limit"), f"{where}.limit"),
            target_pct=_as_float(item.get("target_pct"), f"{where}.target_pct"),
            stop_pct=_as_float(item.get("stop_pct"), f"{where}.stop_pct"),
        ))
    return ParsedReview(session_date=session_date, decided_at=decided_at, decisions=decisions)


def next_session_open(session_date: date) -> datetime:
    """Regular open (09:30 New York) of the session after ``session_date``."""
    from trading_signals.utils.market_calendar import NY_TZ, next_trading_day

    return datetime.combine(next_trading_day(session_date), time(9, 30), tzinfo=NY_TZ)


def is_on_time(decided_at: datetime, file_mtime: datetime, entry_open: datetime) -> bool:
    """True if the decision and the last file write precede the entry open."""
    return decided_at < entry_open and file_mtime < entry_open


def pack_ranks(day_dir: Path) -> dict[str, int]:
    """Ticker → rank from the candidate files ``NN_TICKER.md`` of a pack."""
    ranks: dict[str, int] = {}
    for p in day_dir.glob("[0-9][0-9]_*.md"):
        m = _PACK_FILE_RE.match(p.name)
        if m and int(m.group(1)) > 0:
            ranks[m.group(2)] = int(m.group(1))
    return ranks


def ingest_decisions(
    session: Session,
    root: Path,
    today: date | None = None,
    lookback_days: int = LOOKBACK_DAYS,
    open_fn=next_session_open,
) -> IngestResult:
    """Read new/changed ``decisions.yaml`` files below ``root`` into the DB.

    Unchanged files (same SHA-256) are skipped. A changed file replaces the
    stored review of that day (its ``file_mtime`` decides ``on_time``).
    Invalid files are reported in ``errors`` and leave the stored state as is.
    """
    result = IngestResult()
    if not root.is_dir():
        logger.warning(f"[stage2_review] context pack folder missing: {root}")
        return result
    today = today or datetime.now(APP_TIMEZONE).date()
    since = today - timedelta(days=lookback_days)

    for day_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            folder_date = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        path = day_dir / DECISIONS_FILE
        if folder_date < since or not path.is_file():
            continue
        result.files += 1
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        existing = session.get(Stage2Review, folder_date)
        if existing is not None and existing.content_sha256 == sha:
            result.unchanged += 1
            continue
        try:
            parsed = parse_decisions(raw.decode("utf-8-sig"), folder_date)
        except (DecisionFileError, UnicodeDecodeError) as e:
            msg = f"{folder_date}: {e}"
            logger.warning(f"[stage2_review] invalid {path}: {e}")
            result.errors.append(msg)
            continue

        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        on_time = is_on_time(parsed.decided_at, mtime, open_fn(folder_date))
        ranks = pack_ranks(day_dir)
        if existing is not None:
            session.execute(
                delete(Stage2Decision).where(Stage2Decision.session_date == folder_date)
            )
            session.delete(existing)
            session.flush()
        session.add(Stage2Review(
            session_date=folder_date,
            decided_at=parsed.decided_at,
            file_mtime=mtime,
            on_time=on_time,
            n_buy=sum(d.action == "buy" for d in parsed.decisions),
            n_no_entry=sum(d.action == "no_entry" for d in parsed.decisions),
            content_sha256=sha,
            source_file=str(path)[:300],
        ))
        session.flush()
        for d in parsed.decisions:
            session.add(Stage2Decision(
                session_date=folder_date,
                ticker=d.ticker,
                action=d.action,
                pack_rank=ranks.get(d.ticker),
                reason=d.reason,
                limit_price=d.limit_price,
                target_pct=d.target_pct,
                stop_pct=d.stop_pct,
            ))
        session.flush()
        result.ingested += 1
        logger.info(
            f"[stage2_review] {folder_date}: {len(parsed.decisions)} decisions "
            f"({'on time' if on_time else 'LATE'})"
        )
    return result
