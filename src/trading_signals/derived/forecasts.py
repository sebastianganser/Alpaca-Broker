"""Forecasts: read ``forecasts.yaml`` from the context packs (``docs/FORECASTS.md``).

The Claude skill reviews a context pack and writes
``context_packs/<YYYY-MM-DD>/forecasts.yaml`` (schema ``forecasts/v1``). The
nightly step ``forecast_review`` reads every such file, validates it and
stores it append-only in ``signals.forecast_reviews`` / ``signals.forecasts``.

* :func:`parse_forecasts` - validate one file (pure). A format violation of the
  header (YAML, schema, session, ``decided_at``, ``forecaster_version``,
  ``forecasts``) rejects the file; a violation inside a forecast (forbidden or
  unknown field, enum, range, duplicate, unknown ticker) rejects only that
  row and is reported. Reason: all forecasts of a day are on time or late
  together, and a fixed file is written after the open (late), so dropping the
  valid rows with one typo would lose the whole day.
* :func:`next_session_open` / :func:`is_on_time` - a forecast counts for the
  pre-registered evaluation only if the decision and the last write of the
  file precede the open of the session after the pack day (the evaluated
  return starts at that open). Late forecasts are stored with
  ``on_time = false``.
* :func:`ingest_forecasts` - scan the pack folders; a file is ingested once
  (SHA-256), a changed file for the same session is a new review (append-only).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Protocol

import yaml

from trading_signals.analysis.screen import GROUP_NAMES
from trading_signals.db.base import APP_TIMEZONE
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

SCHEMA_ID = "forecasts/v1"
FORECASTS_FILE = "forecasts.yaml"
HORIZONS = (5, 20, 60)
DIRECTIONS = ("up", "down", "neutral")
MAX_TEXT_LEN = 1000
MAX_VERSION_LEN = 64
MAX_DRIVERS = 5
MIN_CONFIDENCE, MAX_CONFIDENCE = 0.50, 0.95
MAX_EXPECTED_PCT = 1000.0
#: Errors kept per review row (a broken file can have hundreds).
MAX_STORED_ERRORS = 100

#: Controlled vocabulary of ``drivers``: the screen groups plus context tags.
DRIVER_TAGS: tuple[str, ...] = (
    *GROUP_NAMES,
    "news",
    "earnings",
    "sector",
    "macro",
    "other",
)

#: Fields of a forecast row.
ROW_FIELDS: frozenset[str] = frozenset(
    {
        "ticker",
        "horizon_days",
        "direction",
        "confidence",
        "conviction",
        "expected_pct",
        "drivers",
        "reason",
        "risks",
    }
)
#: Strategy fields that belong to the bot (rejected with a specific message).
FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "limit",
        "limit_price",
        "entry",
        "exit",
        "target",
        "target_pct",
        "target_price",
        "price_target",
        "stop",
        "stop_pct",
        "stop_price",
        "stop_loss",
        "take_profit",
        "trailing",
        "size",
        "position",
        "position_size",
        "quantity",
        "qty",
        "shares",
        "weight",
        "leverage",
        "order",
        "action",
        "buy",
        "sell",
    }
)

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_REJECTED = "rejected"

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class ForecastFileError(ValueError):
    """Invalid ``forecasts.yaml`` header (message is shown in the job log)."""


class RowError(ValueError):
    """Invalid forecast row (the row is rejected, the file is kept)."""


@dataclass(frozen=True)
class ParsedForecast:
    ticker: str
    horizon_days: int
    direction: str
    confidence: float | None = None
    conviction: int | None = None
    expected_pct: float | None = None
    drivers: tuple[str, ...] = ()
    reason: str | None = None
    risks: str | None = None
    index: int = 0  # 1-based position in the file


@dataclass(frozen=True)
class RejectedRow:
    """A forecast row that failed validation."""

    index: int  # 1-based position in the file
    ticker: str | None
    horizon_days: int | None
    reason: str

    def message(self) -> str:
        what = " ".join(
            p
            for p in (
                self.ticker,
                f"{self.horizon_days}d" if self.horizon_days else None,
            )
            if p
        )
        return (
            f"forecasts[{self.index}]"
            + (f" ({what})" if what else "")
            + f": {self.reason}"
        )


@dataclass(frozen=True)
class ParsedFile:
    session_date: date
    decided_at: datetime  # timezone-aware
    forecaster_version: str
    forecasts: list[ParsedForecast]
    rejected: list[RejectedRow] = field(default_factory=list)
    schema: str = SCHEMA_ID

    @property
    def status(self) -> str:
        if not self.rejected:
            return STATUS_OK
        return STATUS_PARTIAL if self.forecasts else STATUS_REJECTED


# ── Parsing ──────────────────────────────────────────────────────────


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
    raise ForecastFileError(f"{what}: kein Datum (JJJJ-MM-TT): {val!r}")


def _as_datetime(val, what: str) -> datetime:
    if isinstance(val, str):
        try:
            val = datetime.fromisoformat(val.strip())
        except ValueError as e:
            raise ForecastFileError(
                f"{what}: kein Zeitpunkt (ISO 8601): {val!r}"
            ) from e
    if not isinstance(val, datetime):
        raise ForecastFileError(f"{what}: kein Zeitpunkt (ISO 8601): {val!r}")
    # without a zone the skill means German wall-clock time
    return val if val.tzinfo else val.replace(tzinfo=APP_TIMEZONE)


def _number(val, what: str) -> float:
    if isinstance(val, bool):
        raise RowError(f"{what}: keine Zahl: {val!r}")
    try:
        f = float(val)
    except (TypeError, ValueError) as e:
        raise RowError(f"{what}: keine Zahl: {val!r}") from e
    if not math.isfinite(f):
        raise RowError(f"{what}: keine Zahl: {val!r}")
    return f


def _integer(val, what: str) -> int:
    f = _number(val, what)
    if f != int(f):
        raise RowError(f"{what}: keine ganze Zahl: {val!r}")
    return int(f)


def _text(val, what: str) -> str | None:
    if val is None:
        return None
    if not isinstance(val, str | int | float):
        raise RowError(f"{what}: kein Text: {val!r}")
    text = str(val).strip()
    return text[:MAX_TEXT_LEN] if text else None


def _drivers(val) -> tuple[str, ...]:
    if val is None:
        return ()
    if not isinstance(val, list):
        raise RowError("drivers: muss eine Liste sein")
    if len(val) > MAX_DRIVERS:
        raise RowError(f"drivers: höchstens {MAX_DRIVERS} Einträge, sind {len(val)}")
    tags: list[str] = []
    for item in val:
        tag = str(item).strip().lower()
        if tag not in DRIVER_TAGS:
            raise RowError(
                f"drivers: unbekannter Treiber {item!r} "
                f"(erlaubt: {', '.join(DRIVER_TAGS)})"
            )
        if tag not in tags:
            tags.append(tag)
    return tuple(tags)


def _parse_row(item, index: int = 0) -> ParsedForecast:
    """Validate one forecast row; raises :class:`RowError`."""
    if not isinstance(item, dict):
        raise RowError("Eintrag muss ein Objekt sein")
    keys = {str(k).strip().lower(): k for k in item}
    forbidden = sorted(k for k in keys if k in FORBIDDEN_FIELDS)
    if forbidden:
        raise RowError(
            f"Feld(er) {', '.join(forbidden)} nicht erlaubt - Limit, Ziel, Stop, "
            "Größe und Orders sind Sache des Bots, nicht der Prognose"
        )
    unknown = sorted(k for k in keys if k not in ROW_FIELDS)
    if unknown:
        raise RowError(
            f"unbekannte(s) Feld(er) {', '.join(unknown)} (erlaubt: "
            f"{', '.join(sorted(ROW_FIELDS))})"
        )
    get = {k: item[orig] for k, orig in keys.items()}

    ticker = str(get.get("ticker") or "").strip().upper()
    if not _TICKER_RE.match(ticker):
        raise RowError(f"ungültiger Ticker {get.get('ticker')!r}")
    if "horizon_days" not in get:
        raise RowError("horizon_days fehlt")
    horizon = _integer(get["horizon_days"], "horizon_days")
    if horizon not in HORIZONS:
        raise RowError(
            f"horizon_days muss {', '.join(map(str, HORIZONS))} sein, ist {horizon}"
        )
    direction = str(get.get("direction") or "").strip().lower()
    if direction not in DIRECTIONS:
        raise RowError(
            f"direction muss up, down oder neutral sein, ist {get.get('direction')!r}"
        )

    confidence = conviction = expected = None
    if direction == "neutral":
        given = [
            k
            for k in ("confidence", "conviction", "expected_pct")
            if get.get(k) is not None
        ]
        if given:
            raise RowError(f"{', '.join(given)} ist für neutral nicht vorgesehen")
    else:
        if get.get("confidence") is None:
            raise RowError(f"confidence fehlt (Pflicht für {direction})")
        confidence = _number(get["confidence"], "confidence")
        if not MIN_CONFIDENCE <= confidence <= MAX_CONFIDENCE:
            raise RowError(
                f"confidence muss {MIN_CONFIDENCE:.2f} … {MAX_CONFIDENCE:.2f} sein "
                f"(Wahrscheinlichkeit, dass die Richtung stimmt), ist {confidence}"
            )
        if get.get("conviction") is None:
            raise RowError(f"conviction fehlt (Pflicht für {direction})")
        conviction = _integer(get["conviction"], "conviction")
        if not 1 <= conviction <= 5:
            raise RowError(f"conviction muss 1 … 5 sein, ist {conviction}")
        if get.get("expected_pct") is not None:
            expected = _number(get["expected_pct"], "expected_pct")
            if abs(expected) > MAX_EXPECTED_PCT:
                raise RowError(
                    f"expected_pct außerhalb ±{MAX_EXPECTED_PCT:g} %: {expected}"
                )
            if (direction == "up" and expected <= 0) or (
                direction == "down" and expected >= 0
            ):
                raise RowError(
                    f"expected_pct {expected} passt nicht zu direction {direction} "
                    "(positiv für up, negativ für down)"
                )
    return ParsedForecast(
        ticker=ticker,
        horizon_days=horizon,
        direction=direction,
        confidence=confidence,
        conviction=conviction,
        expected_pct=expected,
        drivers=_drivers(get.get("drivers")),
        reason=_text(get.get("reason"), "reason"),
        risks=_text(get.get("risks"), "risks"),
        index=index,
    )


def parse_forecasts(text: str, folder_date: date) -> ParsedFile:
    """Validate the content of one ``forecasts.yaml`` (pure).

    Args:
        text: File content.
        folder_date: Date of the pack folder; ``session`` must match it.

    Returns:
        The accepted forecasts and the rejected rows (with reasons).

    Raises:
        ForecastFileError: Header violation, the whole file is rejected
            (German message).
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ForecastFileError(f"kein gültiges YAML: {e}") from e
    if not isinstance(doc, dict):
        raise ForecastFileError("Datei muss ein YAML-Objekt sein")
    if doc.get("schema") != SCHEMA_ID:
        raise ForecastFileError(
            f"schema muss '{SCHEMA_ID}' sein, ist {doc.get('schema')!r}"
        )
    if "session" not in doc:
        raise ForecastFileError("Feld 'session' fehlt")
    session_date = _as_date(doc["session"], "session")
    if session_date != folder_date:
        raise ForecastFileError(
            f"session {session_date} passt nicht zum Ordner {folder_date}"
        )
    if "decided_at" not in doc:
        raise ForecastFileError("Feld 'decided_at' fehlt")
    decided_at = _as_datetime(doc["decided_at"], "decided_at")
    version = doc.get("forecaster_version")
    if not isinstance(version, str) or not version.strip():
        raise ForecastFileError(
            "Feld 'forecaster_version' fehlt oder ist leer "
            "(Skill-/Prompt-Version + Modell)"
        )
    version = version.strip()
    if len(version) > MAX_VERSION_LEN:
        raise ForecastFileError(
            f"forecaster_version hat {len(version)} Zeichen "
            f"(höchstens {MAX_VERSION_LEN})"
        )
    raw = doc.get("forecasts")
    if not isinstance(raw, list):
        raise ForecastFileError(
            "'forecasts' muss eine Liste sein (leer: forecasts: [])"
        )

    accepted: list[tuple[int, ParsedForecast]] = []
    rejected: list[RejectedRow] = []
    for i, item in enumerate(raw, 1):
        try:
            accepted.append((i, _parse_row(item, i)))
        except RowError as e:
            ticker = horizon = None
            if isinstance(item, dict):
                t = str(item.get("ticker") or "").strip().upper()
                ticker = t or None
                h = item.get("horizon_days")
                horizon = h if isinstance(h, int) and not isinstance(h, bool) else None
            rejected.append(RejectedRow(i, ticker, horizon, str(e)))

    # one forecast per ticker x horizon: which of two conflicting calls would
    # count is arbitrary, so every row of a duplicated key is rejected
    counts: dict[tuple[str, int], int] = {}
    for _, f in accepted:
        key = (f.ticker, f.horizon_days)
        counts[key] = counts.get(key, 0) + 1
    kept: list[ParsedForecast] = []
    for i, f in accepted:
        if counts[(f.ticker, f.horizon_days)] > 1:
            rejected.append(
                RejectedRow(
                    i,
                    f.ticker,
                    f.horizon_days,
                    "doppelt: je Ticker und Horizont nur eine Prognose pro Datei",
                )
            )
        else:
            kept.append(f)
    rejected.sort(key=lambda r: r.index)
    return ParsedFile(
        session_date=session_date,
        decided_at=decided_at,
        forecaster_version=version,
        forecasts=kept,
        rejected=rejected,
    )


# ── On-time rule ─────────────────────────────────────────────────────


def next_session_open(session_date: date) -> datetime:
    """Regular open (09:30 New York) of the session after ``session_date``.

    The evaluated return starts here (``open(d+1)``); a forecast made after
    this moment is late.
    """
    from trading_signals.utils.market_calendar import NY_TZ, next_trading_day

    return datetime.combine(next_trading_day(session_date), time(9, 30), tzinfo=NY_TZ)


def is_on_time(
    decided_at: datetime, file_mtime: datetime, entry_open: datetime
) -> bool:
    """True if the decision and the last file write precede the entry open."""
    return decided_at < entry_open and file_mtime < entry_open


# ── Storage ──────────────────────────────────────────────────────────


@dataclass
class ReviewRecord:
    """The review row to store (one file)."""

    session_date: date
    file_path: str
    file_sha256: str
    file_mtime: datetime
    schema: str | None
    forecaster_version: str | None
    decided_at: datetime | None
    on_time: bool
    status: str
    n_forecasts: int
    n_rejected: int
    errors: list[str]


@dataclass
class ForecastRecord:
    """The forecast row to store (denormalised from its review)."""

    session_date: date
    ticker: str
    horizon_days: int
    direction: str
    confidence: float | None
    conviction: int | None
    expected_pct: float | None
    drivers: list[str]
    reason: str | None
    risks: str | None
    forecaster_version: str
    decided_at: datetime
    valid_from: datetime
    on_time: bool


class ForecastStore(Protocol):
    """What :func:`ingest_forecasts` needs from the database."""

    def known_hashes(self) -> set[str]: ...

    def session_tickers(self, session_date: date) -> set[str]: ...

    def add_review(
        self, review: ReviewRecord, forecasts: Sequence[ForecastRecord]
    ) -> int: ...


class SqlForecastStore:
    """:class:`ForecastStore` on a SQLAlchemy session (the caller commits)."""

    def __init__(self, session) -> None:
        self.session = session

    def known_hashes(self) -> set[str]:
        from sqlalchemy import select

        from trading_signals.db.models.forecasts import ForecastReview

        return set(self.session.execute(select(ForecastReview.file_sha256)).scalars())

    def session_tickers(self, session_date: date) -> set[str]:
        from sqlalchemy import select

        from trading_signals.db.models.features import FeatureSnapshot

        return set(
            self.session.execute(
                select(FeatureSnapshot.ticker).where(
                    FeatureSnapshot.snapshot_date == session_date
                )
            ).scalars()
        )

    def add_review(
        self, review: ReviewRecord, forecasts: Sequence[ForecastRecord]
    ) -> int:
        from trading_signals.db.models.forecasts import Forecast, ForecastReview

        row = ForecastReview(
            session_date=review.session_date,
            file_path=review.file_path[:300],
            file_sha256=review.file_sha256,
            file_mtime=review.file_mtime,
            schema=review.schema,
            forecaster_version=review.forecaster_version,
            decided_at=review.decided_at,
            on_time=review.on_time,
            status=review.status,
            n_forecasts=review.n_forecasts,
            n_rejected=review.n_rejected,
            errors=review.errors or None,
        )
        self.session.add(row)
        self.session.flush()
        for f in forecasts:
            self.session.add(
                Forecast(
                    review_id=row.id,
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
        self.session.flush()
        return row.id


# ── Ingest ───────────────────────────────────────────────────────────


@dataclass
class IngestResult:
    files: int = 0
    ingested: int = 0
    unchanged: int = 0
    forecasts: int = 0
    late: int = 0
    rejected_files: int = 0
    rejected_rows: int = 0
    errors: list[str] = field(default_factory=list)

    def notes(self) -> str:
        text = (
            f"files={self.files}, ingested={self.ingested}, "
            f"unchanged={self.unchanged}, "
            f"forecasts={self.forecasts}, late={self.late}, "
            f"rejected_files={self.rejected_files}, rejected_rows={self.rejected_rows}"
        )
        if self.errors:
            text += "; " + "; ".join(self.errors[:5])
            if len(self.errors) > 5:
                text += f"; (+{len(self.errors) - 5} weitere)"
        return text


def _check_tickers(
    parsed: ParsedFile, known: set[str]
) -> tuple[list[ParsedForecast], list[RejectedRow]]:
    """Reject forecasts of tickers without a feature snapshot on the session day.

    Such a forecast could never be scored. An empty ``known`` set means the
    snapshots of the session do not exist (yet): no judgement is possible and
    every row is accepted.
    """
    if not known:
        return parsed.forecasts, []
    keep: list[ParsedForecast] = []
    bad: list[RejectedRow] = []
    for f in parsed.forecasts:
        if f.ticker in known:
            keep.append(f)
        else:
            bad.append(
                RejectedRow(
                    f.index,
                    f.ticker,
                    f.horizon_days,
                    f"{f.ticker} hat am {parsed.session_date} keinen Feature-Snapshot "
                    "(nicht im Universum) und wäre nie auswertbar",
                )
            )
    return keep, bad


def ingest_forecasts(
    store: ForecastStore,
    root: Path,
    open_fn: Callable[[date], datetime] = next_session_open,
) -> IngestResult:
    """Read new ``forecasts.yaml`` files below ``root`` into the store.

    A file whose SHA-256 is known is skipped (idempotent). A changed file for
    the same session is a new review - forecasts are append-only, the
    evaluation picks the latest on-time review per session
    (``analysis/forecast_review.effective_forecasts``). Invalid files are
    stored as ``rejected`` reviews (reported once, not on every night), invalid
    rows are dropped from an otherwise valid file; both are listed in
    ``errors``.
    """
    result = IngestResult()
    if not root.is_dir():
        logger.warning(f"[forecast_review] context pack folder missing: {root}")
        return result
    known_hashes = store.known_hashes()

    for day_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            folder_date = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        path = day_dir / FORECASTS_FILE
        if not path.is_file():
            continue
        result.files += 1
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if sha in known_hashes:
            result.unchanged += 1
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        entry_open = open_fn(folder_date)

        try:
            parsed = parse_forecasts(raw.decode("utf-8-sig"), folder_date)
        except (ForecastFileError, UnicodeDecodeError) as e:
            msg = f"{folder_date}: {e}"
            logger.warning(f"[forecast_review] invalid {path}: {e}")
            result.errors.append(msg)
            result.rejected_files += 1
            store.add_review(
                ReviewRecord(
                    session_date=folder_date,
                    file_path=str(path),
                    file_sha256=sha,
                    file_mtime=mtime,
                    schema=None,
                    forecaster_version=None,
                    decided_at=None,
                    on_time=False,
                    status=STATUS_REJECTED,
                    n_forecasts=0,
                    n_rejected=0,
                    errors=[str(e)],
                ),
                [],
            )
            known_hashes.add(sha)
            continue

        kept, unknown = _check_tickers(parsed, store.session_tickers(folder_date))
        rejected = sorted([*parsed.rejected, *unknown], key=lambda r: r.index)
        on_time = is_on_time(parsed.decided_at, mtime, entry_open)
        status = (
            STATUS_OK if not rejected else (STATUS_PARTIAL if kept else STATUS_REJECTED)
        )
        messages = [r.message() for r in rejected]
        records = [
            ForecastRecord(
                session_date=folder_date,
                ticker=f.ticker,
                horizon_days=f.horizon_days,
                direction=f.direction,
                confidence=f.confidence,
                conviction=f.conviction,
                expected_pct=f.expected_pct,
                drivers=list(f.drivers),
                reason=f.reason,
                risks=f.risks,
                forecaster_version=parsed.forecaster_version,
                decided_at=parsed.decided_at,
                valid_from=entry_open,
                on_time=on_time,
            )
            for f in kept
        ]
        store.add_review(
            ReviewRecord(
                session_date=folder_date,
                file_path=str(path),
                file_sha256=sha,
                file_mtime=mtime,
                schema=parsed.schema,
                forecaster_version=parsed.forecaster_version,
                decided_at=parsed.decided_at,
                on_time=on_time,
                status=status,
                n_forecasts=len(kept),
                n_rejected=len(rejected),
                errors=messages[:MAX_STORED_ERRORS],
            ),
            records,
        )
        known_hashes.add(sha)
        result.ingested += 1
        result.forecasts += len(kept)
        result.rejected_rows += len(rejected)
        if not on_time:
            result.late += len(kept)
        result.errors.extend(f"{folder_date}: {m}" for m in messages)
        logger.info(
            f"[forecast_review] {folder_date}: {len(kept)} forecasts "
            f"({'on time' if on_time else 'LATE'}), {len(rejected)} rejected rows, "
            f"version {parsed.forecaster_version}"
        )
    return result


__all__ = [
    "DRIVER_TAGS",
    "FORBIDDEN_FIELDS",
    "FORECASTS_FILE",
    "SCHEMA_ID",
    "ForecastFileError",
    "ForecastRecord",
    "ForecastStore",
    "IngestResult",
    "ParsedFile",
    "ParsedForecast",
    "RejectedRow",
    "ReviewRecord",
    "RowError",
    "SqlForecastStore",
    "ingest_forecasts",
    "is_on_time",
    "next_session_open",
    "parse_forecasts",
]
