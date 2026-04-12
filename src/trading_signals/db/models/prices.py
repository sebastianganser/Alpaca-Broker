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
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class PriceDaily(Base):
    """Daily OHLCV price data for a ticker."""

    __tablename__ = "prices_daily"

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
    source: Mapped[str] = mapped_column(String(50), default="yfinance")
    is_extrapolated: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<PriceDaily(ticker={self.ticker!r}, date={self.trade_date}, "
            f"close={self.close}, extrapolated={self.is_extrapolated})>"
        )
