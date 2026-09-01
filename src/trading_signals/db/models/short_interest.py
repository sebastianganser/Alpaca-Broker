"""ORM model for short interest data.

ShortVolume: Daily short volume from FINRA (via Massive/Polygon).
ShortInterest: Bi-monthly FINRA short interest settlement data.

Source: Massive API (https://massive.com)
Sprint: 9.5c (B5 Short Interest Collector)
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class ShortVolume(Base):
    """Daily short volume from FINRA (via Massive/Polygon)."""
    __tablename__ = "short_volume"
    __table_args__ = (
        UniqueConstraint("ticker", "trade_date", name="uq_short_volume_dedup"),
        Index("idx_short_volume_ticker", "ticker"),
        Index("idx_short_volume_date", "trade_date"),
        {"schema": "signals"},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    short_volume: Mapped[int | None] = mapped_column(BigInteger)
    total_volume: Mapped[int | None] = mapped_column(BigInteger)
    short_volume_ratio: Mapped[float | None] = mapped_column(Numeric(6, 4))
    source: Mapped[str] = mapped_column(String(50), default="massive")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ShortInterest(Base):
    """Bi-monthly FINRA short interest settlement data."""
    __tablename__ = "short_interest"
    __table_args__ = (
        UniqueConstraint("ticker", "settlement_date", "source", name="uq_short_interest_dedup"),
        Index("idx_short_interest_ticker", "ticker"),
        {"schema": "signals"},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    short_interest: Mapped[int | None] = mapped_column(BigInteger)
    avg_daily_volume: Mapped[int | None] = mapped_column(BigInteger)
    days_to_cover: Mapped[float | None] = mapped_column(Numeric(10, 4))
    source: Mapped[str] = mapped_column(String(50), default="massive")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
