"""ORM models of the forecast pipeline (migration 039, ``docs/FORECASTS.md``).

* :class:`ForecastReview` - one ingested ``forecasts.yaml`` (idempotent by file hash).
* :class:`Forecast` - append-only, one row per ticker x horizon of a file.
* :class:`PackCandidate` - the screen candidates of a context pack (written by
  the pack generator), the benchmark of the value-add comparison.
* :class:`ForecastCriterionSample` - the frozen decisive sample of the
  pre-registered criterion and its verdict, each written ONCE.
* :class:`ForecastFileSeal` - SHA-256 of a ``forecasts.yaml`` as it stood
  before the open (the scheduler job ``forecast_seal``); the on-time proof.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base, TZDateTime

REVIEW_STATUSES = ("ok", "partial", "rejected")
DIRECTIONS = ("up", "down", "neutral")
HORIZONS = (5, 20, 60)
BUCKETS = ("bull", "bear", "random", "requested")
CRITERION_MODES = ("system", "claude_value_add")


def _in_list(column: str, values: tuple) -> str:
    items = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in values)
    return f"{column} IN ({items})"


class ForecastReview(Base):
    """One ingested ``forecasts.yaml`` of a pack day.

    A changed file for the same session is a NEW review (append-only); the
    evaluation uses the latest review per session and ``on_time`` flag
    (``analysis/forecast_review.py``). A file that fails the header validation
    is stored with ``status = 'rejected'`` and its errors, so it is reported
    once and not on every night.
    """

    __tablename__ = "forecast_reviews"
    __table_args__ = (
        CheckConstraint(_in_list("status", REVIEW_STATUSES), name="status"),
        UniqueConstraint("file_sha256", name="uq_forecast_reviews_file_sha256"),
        Index("ix_forecast_reviews_session_date", "session_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    file_path: Mapped[str] = mapped_column(String(300), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # last write of the file: part of the on-time rule
    file_mtime: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    schema: Mapped[str | None] = mapped_column(String(40))
    forecaster_version: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    # decided_at and file_mtime before the open of the next session
    on_time: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    n_forecasts: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    n_rejected: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    errors: Mapped[list | None] = mapped_column(JSONB)
    ingested_at: Mapped[datetime] = mapped_column(
        TZDateTime(), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<ForecastReview({self.session_date}, {self.status}, "
            f"n={self.n_forecasts}, rejected={self.n_rejected}, "
            f"on_time={self.on_time})>"
        )


class Forecast(Base):
    """One forecast: ticker x horizon of a review (append-only)."""

    __tablename__ = "forecasts"
    __table_args__ = (
        CheckConstraint(_in_list("horizon_days", HORIZONS), name="horizon_days"),
        CheckConstraint(_in_list("direction", DIRECTIONS), name="direction"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.5 AND confidence <= 0.95)",
            name="confidence",
        ),
        CheckConstraint(
            "conviction IS NULL OR (conviction >= 1 AND conviction <= 5)",
            name="conviction",
        ),
        # an up / down call states its probability and its strength
        CheckConstraint(
            "direction = 'neutral' OR "
            "(confidence IS NOT NULL AND conviction IS NOT NULL)",
            name="directional_scored",
        ),
        UniqueConstraint(
            "review_id",
            "ticker",
            "horizon_days",
            name="uq_forecasts_review_ticker_horizon",
        ),
        Index("ix_forecasts_session_date_horizon_days", "session_date", "horizon_days"),
        Index(
            "ix_forecasts_forecaster_version_horizon_days",
            "forecaster_version",
            "horizon_days",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("signals.forecast_reviews.id"), nullable=False
    )
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    horizon_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    direction: Mapped[str] = mapped_column(String(7), nullable=False)
    # P(direction correct), 0.50 .. 0.95; NULL for neutral
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    conviction: Mapped[int | None] = mapped_column(SmallInteger)
    # signed expected raw return over the horizon, percent
    expected_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    drivers: Mapped[list[str] | None] = mapped_column(ARRAY(String(20)))
    reason: Mapped[str | None] = mapped_column(Text)
    risks: Mapped[str | None] = mapped_column(Text)
    forecaster_version: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    # open of the first session after session_date: the evaluated return starts here
    valid_from: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    on_time: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<Forecast({self.session_date}, {self.ticker}, {self.horizon_days}d, "
            f"{self.direction}, on_time={self.on_time})>"
        )


class PackCandidate(Base):
    """A ticker the screen put into a bucket of the context pack of a session."""

    __tablename__ = "pack_candidates"
    __table_args__ = (
        PrimaryKeyConstraint(
            "session_date", "ticker", "bucket", name="pk_pack_candidates"
        ),
        CheckConstraint(_in_list("bucket", BUCKETS), name="bucket"),
    )

    session_date: Mapped[date] = mapped_column(Date)
    ticker: Mapped[str] = mapped_column(String(20))
    # bull | bear | random | requested (docs/SCREEN.md section 6)
    bucket: Mapped[str] = mapped_column(String(10))
    bucket_rank: Mapped[int | None] = mapped_column(SmallInteger)
    screen_version: Mapped[str] = mapped_column(String(20), nullable=False)
    gate_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    composite: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    evidence_breadth: Mapped[int | None] = mapped_column(SmallInteger)
    # {group: score} and {flag: value} of the screen result
    group_scores: Mapped[dict | None] = mapped_column(JSONB)
    flags: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<PackCandidate({self.session_date}, {self.ticker}, {self.bucket})>"


class ForecastCriterionSample(Base):
    """Frozen decisive sample of the pre-registered criterion and its verdict.

    Written ONCE: the sample (sessions, forecast ids) when it freezes, the
    verdict together with the per-forecast values it was computed from
    (``verdict_values``) when the windows are realised. Later reports show
    these rows and never recompute the verdict from live returns
    (``docs/FORECASTS.md`` section 6).
    """

    __tablename__ = "forecast_criterion_samples"
    __table_args__ = (
        CheckConstraint(
            _in_list("criterion_mode", CRITERION_MODES), name="criterion_mode"
        ),
        # verdict, its values and its timestamp exist together or not at all
        CheckConstraint(
            "(verdict IS NULL) = (verdict_values IS NULL) "
            "AND (verdict IS NULL) = (verdict_at IS NULL)",
            name="verdict_complete",
        ),
        # one sample per forecaster version and horizon
        UniqueConstraint(
            "horizon",
            "forecaster_version",
            name="uq_forecast_criterion_samples_horizon_version",
        ),
    )

    # SHA-256 key of the frozen sample (analysis.forecast_eval.sample_key)
    sample_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    horizon: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    forecaster_version: Mapped[str] = mapped_column(String(64), nullable=False)
    criterion_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    # registered constants in force when the sample froze
    min_sessions: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    min_per_side: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    n_up: Mapped[int] = mapped_column(Integer, nullable=False)
    n_down: Mapped[int] = mapped_column(Integer, nullable=False)
    sessions: Mapped[list] = mapped_column(JSONB, nullable=False)
    forecast_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(
        TZDateTime(), nullable=False, server_default=func.now()
    )
    # per-forecast values the verdict is computed from (VALUE_COLUMNS)
    verdict_values: Mapped[list | None] = mapped_column(JSONB)
    verdict: Mapped[dict | None] = mapped_column(JSONB)
    verdict_at: Mapped[datetime | None] = mapped_column(TZDateTime())

    def __repr__(self) -> str:
        state = "open" if self.verdict is None else str(self.verdict.get("status"))
        return (
            f"<ForecastCriterionSample({self.horizon}d, {self.forecaster_version}, "
            f"{self.sample_key[:8]}, {state})>"
        )


class ForecastFileSeal(Base):
    """A ``forecasts.yaml`` as it stood before the open of the next session.

    The scheduler job ``forecast_seal`` (09:00 / 09:15 / 09:25 New York on NYSE
    sessions) stores the SHA-256 and size of the file of the pack whose
    deadline is the coming 09:30 open. The ingest counts a file as *on time*
    only if its hash was sealed before that open: file mtime and ``decided_at``
    are chosen by the writer, ``sealed_at`` is not (``docs/FORECASTS.md``
    section 2). Append-only; one row per distinct content per session.
    """

    __tablename__ = "forecast_file_seals"
    __table_args__ = (
        UniqueConstraint(
            "session_date",
            "file_sha256",
            name="uq_forecast_file_seals_session_date_file_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    file_path: Mapped[str] = mapped_column(String(300), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # timezone-aware on purpose (not TZDateTime): compared with the 09:30 New
    # York open without any wall-clock conversion
    sealed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<ForecastFileSeal({self.session_date}, {self.file_sha256[:8]}, "
            f"{self.sealed_at})>"
        )
