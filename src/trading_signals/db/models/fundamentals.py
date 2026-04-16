"""ORM models for fundamentals, analyst ratings, and earnings calendar data.

FundamentalsSnapshot: Raw layer – daily/weekly snapshot of key financial metrics.
AnalystRating:        Raw layer – individual analyst upgrades/downgrades.
EarningsCalendar:     Raw layer – earnings dates with EPS estimates and surprises.

All data sourced from yfinance (Sprint 5).
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class FundamentalsSnapshot(Base):
    """Weekly snapshot of fundamental data for a single ticker."""

    __tablename__ = "fundamentals_snapshot"
    __table_args__ = (
        {"schema": "signals"},
    )

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    market_cap: Mapped[float | None] = mapped_column(Numeric(24, 2))
    pe_ratio: Mapped[float | None] = mapped_column(Numeric(16, 4))
    forward_pe: Mapped[float | None] = mapped_column(Numeric(16, 4))
    ps_ratio: Mapped[float | None] = mapped_column(Numeric(16, 4))
    pb_ratio: Mapped[float | None] = mapped_column(Numeric(16, 4))
    ev_ebitda: Mapped[float | None] = mapped_column(Numeric(16, 4))
    profit_margin: Mapped[float | None] = mapped_column(Numeric(10, 6))
    operating_margin: Mapped[float | None] = mapped_column(Numeric(10, 6))
    return_on_equity: Mapped[float | None] = mapped_column(Numeric(10, 6))
    revenue_ttm: Mapped[float | None] = mapped_column(Numeric(20, 2))
    revenue_growth_yoy: Mapped[float | None] = mapped_column(Numeric(10, 6))
    eps_ttm: Mapped[float | None] = mapped_column(Numeric(16, 4))
    eps_growth_yoy: Mapped[float | None] = mapped_column(Numeric(10, 6))
    debt_to_equity: Mapped[float | None] = mapped_column(Numeric(16, 4))
    current_ratio: Mapped[float | None] = mapped_column(Numeric(16, 4))
    dividend_yield: Mapped[float | None] = mapped_column(Numeric(10, 6))
    beta: Mapped[float | None] = mapped_column(Numeric(10, 4))
    # Analyst consensus price targets (from yfinance analyst_price_targets)
    target_price_low: Mapped[float | None] = mapped_column(Numeric(16, 4))
    target_price_mean: Mapped[float | None] = mapped_column(Numeric(16, 4))
    target_price_median: Mapped[float | None] = mapped_column(Numeric(16, 4))
    target_price_high: Mapped[float | None] = mapped_column(Numeric(16, 4))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<FundamentalsSnapshot(ticker={self.ticker!r}, "
            f"date={self.snapshot_date}, pe={self.pe_ratio}, "
            f"mcap={self.market_cap})>"
        )


class AnalystRating(Base):
    """An individual analyst upgrade/downgrade rating change."""

    __tablename__ = "analyst_ratings"
    __table_args__ = (
        UniqueConstraint(
            "ticker", "firm", "rating_date", "action",
            name="uq_analyst_rating_dedup",
        ),
        Index("idx_analyst_ticker", "ticker"),
        Index("idx_analyst_rating_date", "rating_date"),
        {"schema": "signals"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(String(20))
    firm: Mapped[str | None] = mapped_column(String(200))
    analyst: Mapped[str | None] = mapped_column(String(200))
    rating_date: Mapped[date | None] = mapped_column(Date)
    rating_new: Mapped[str | None] = mapped_column(String(50))
    rating_old: Mapped[str | None] = mapped_column(String(50))
    price_target_new: Mapped[float | None] = mapped_column(Numeric(16, 4))
    price_target_old: Mapped[float | None] = mapped_column(Numeric(16, 4))
    action: Mapped[str | None] = mapped_column(String(50))  # up, down, main, init, reit
    raw_data: Mapped[dict | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<AnalystRating(ticker={self.ticker!r}, firm={self.firm!r}, "
            f"action={self.action!r}, date={self.rating_date})>"
        )


class EarningsCalendar(Base):
    """Earnings dates with EPS estimates, actuals, and surprise data."""

    __tablename__ = "earnings_calendar"
    __table_args__ = (
        {"schema": "signals"},
    )

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    earnings_date: Mapped[date] = mapped_column(Date, primary_key=True)
    time_of_day: Mapped[str | None] = mapped_column(String(20))  # BMO, AMC
    eps_estimate: Mapped[float | None] = mapped_column(Numeric(16, 4))
    eps_actual: Mapped[float | None] = mapped_column(Numeric(16, 4))
    revenue_estimate: Mapped[float | None] = mapped_column(Numeric(20, 2))
    revenue_actual: Mapped[float | None] = mapped_column(Numeric(20, 2))
    surprise_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<EarningsCalendar(ticker={self.ticker!r}, "
            f"date={self.earnings_date}, eps_est={self.eps_estimate}, "
            f"eps_act={self.eps_actual})>"
        )
