"""ORM models for the stage-2 forward test (migration 035, concept §7.7).

The Claude broker skill writes ``decisions.yaml`` into the context-pack folder
of a pack day; ``derived/stage2_decisions.py`` stores it here. Trade results
are not stored – they come from the barrier label in ``feature_snapshots``.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base, TZDateTime


class Stage2Review(Base):
    """One reviewed pack day (one ``decisions.yaml``)."""

    __tablename__ = "stage2_reviews"

    session_date: Mapped[date] = mapped_column(Date, primary_key=True)
    decided_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    file_mtime: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    # decided_at and file_mtime before the open of the next session
    on_time: Mapped[bool] = mapped_column(Boolean, nullable=False)
    n_buy: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    n_no_entry: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file: Mapped[str] = mapped_column(String(300), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        TZDateTime(), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<Stage2Review({self.session_date}, buy={self.n_buy}, "
            f"no_entry={self.n_no_entry}, on_time={self.on_time})>"
        )


class Stage2Decision(Base):
    """Decision for one ticker on a reviewed pack day."""

    __tablename__ = "stage2_decisions"
    __table_args__ = (
        # naming convention → ck_stage2_decisions_action (as in migration 035)
        CheckConstraint("action IN ('buy', 'no_entry')", name="action"),
        UniqueConstraint("session_date", "ticker", name="uq_stage2_session_ticker"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_date: Mapped[date] = mapped_column(
        Date,
        ForeignKey("signals.stage2_reviews.session_date", ondelete="CASCADE"),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    # rank of the ticker in the context pack (NN_TICKER.md), None if not in the pack
    pack_rank: Mapped[int | None] = mapped_column(SmallInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    target_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    stop_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))

    def __repr__(self) -> str:
        return f"<Stage2Decision({self.session_date}, {self.ticker}, {self.action})>"
