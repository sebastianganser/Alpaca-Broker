"""PriceDaily ORM model – daily OHLCV price data.

Stores one row per ticker per trading day. Composite primary key
ensures uniqueness. The is_extrapolated flag marks data points
that were filled in by the gap detector rather than fetched from
a real data source.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class PriceDaily(Base):
    """Daily OHLCV price data for a ticker."""

    __tablename__ = "prices_daily"
    __table_args__ = (
        Index("idx_prices_date", "trade_date"),
        # Partial index (only gap-filled rows), migration 003
        Index(
            "idx_prices_extrapolated",
            "is_extrapolated",
            postgresql_where=text("is_extrapolated = true"),
        ),
    )

    ticker: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("signals.universe.ticker"),
        primary_key=True,
    )
    trade_date: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
    )
    open: Mapped[float | None] = mapped_column(Numeric(16, 4))
    high: Mapped[float | None] = mapped_column(Numeric(16, 4))
    low: Mapped[float | None] = mapped_column(Numeric(16, 4))
    close: Mapped[float | None] = mapped_column(Numeric(16, 4))
    adj_close: Mapped[float | None] = mapped_column(Numeric(16, 4))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(
        String(50), nullable=True, default="yfinance", server_default="yfinance"
    )
    is_extrapolated: Mapped[bool] = mapped_column(
        Boolean, nullable=True, default=False, server_default=text("false")
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=True, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<PriceDaily(ticker={self.ticker!r}, date={self.trade_date}, "
            f"close={self.close}, extrapolated={self.is_extrapolated})>"
        )
