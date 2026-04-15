"""CollectionLog ORM model – audit trail for collector runs.

Every collector run creates exactly one log entry, tracking timing,
status, record counts, gap statistics, and any errors encountered.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class CollectionLog(Base):
    """Audit log entry for a single collector run."""

    __tablename__ = "collection_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    collector_name: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str | None] = mapped_column(String(20))  # success, partial, failed
    records_fetched: Mapped[int | None] = mapped_column(Integer)
    records_written: Mapped[int | None] = mapped_column(Integer)
    gaps_detected: Mapped[int] = mapped_column(Integer, default=0)
    gaps_repaired: Mapped[int] = mapped_column(Integer, default=0)
    gaps_extrapolated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
    log_lines: Mapped[list | None] = mapped_column(JSONB)

    def __repr__(self) -> str:
        return (
            f"<CollectionLog(id={self.id}, collector={self.collector_name!r}, "
            f"status={self.status!r}, written={self.records_written})>"
        )
