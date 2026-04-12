"""ORM models for ARK ETF data.

ARKHolding: Daily snapshot of all holdings per ETF.
ARKDelta: Derived layer showing daily changes in positions.
"""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class ARKHolding(Base):
    """Daily snapshot of a single holding within an ARK ETF."""

    __tablename__ = "ark_holdings"

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    etf_ticker: Mapped[str] = mapped_column(String(10), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    company_name: Mapped[str | None] = mapped_column(String(200))
    cusip: Mapped[str | None] = mapped_column(String(20))
    shares: Mapped[float | None] = mapped_column(Numeric(20, 4))
    market_value: Mapped[float | None] = mapped_column(Numeric(20, 2))
    weight_pct: Mapped[float | None] = mapped_column(Numeric(8, 4))
    weight_rank: Mapped[int | None] = mapped_column(Integer)
    share_price: Mapped[float | None] = mapped_column(Numeric(16, 4))
    source: Mapped[str] = mapped_column(String(50), default="arkfunds.io")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<ARKHolding(date={self.snapshot_date}, etf={self.etf_ticker!r}, "
            f"ticker={self.ticker!r}, weight={self.weight_pct}%)>"
        )


class ARKDelta(Base):
    """Daily change in an ARK ETF position (derived from holdings snapshots)."""

    __tablename__ = "ark_deltas"

    delta_date: Mapped[date] = mapped_column(Date, primary_key=True)
    etf_ticker: Mapped[str] = mapped_column(String(10), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    delta_type: Mapped[str] = mapped_column(String(20))
    shares_prev: Mapped[float | None] = mapped_column(Numeric(20, 4))
    shares_curr: Mapped[float | None] = mapped_column(Numeric(20, 4))
    shares_delta: Mapped[float | None] = mapped_column(Numeric(20, 4))
    weight_prev: Mapped[float | None] = mapped_column(Numeric(8, 4))
    weight_curr: Mapped[float | None] = mapped_column(Numeric(8, 4))
    weight_delta: Mapped[float | None] = mapped_column(Numeric(8, 4))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<ARKDelta(date={self.delta_date}, etf={self.etf_ticker!r}, "
            f"ticker={self.ticker!r}, type={self.delta_type!r}, "
            f"shares_delta={self.shares_delta})>"
        )
