"""ORM models for SEC Form 4 insider trade data.

InsiderTrade: Raw layer – individual insider transactions from Form 4 filings.
InsiderCluster: Derived layer – cluster detection when multiple insiders
                buy within a rolling window.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class InsiderTrade(Base):
    """A single insider transaction from an SEC Form 4 filing."""

    __tablename__ = "insider_trades"
    __table_args__ = (
        UniqueConstraint(
            "cik", "insider_name", "transaction_date",
            "transaction_type", "shares", "price_per_share",
            name="uq_insider_trade_dedup",
        ),
        Index("idx_insider_ticker_date", "ticker", "transaction_date"),
        Index("idx_insider_filing_date", "filing_date"),
        {"schema": "signals"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(String(20))
    company_name: Mapped[str | None] = mapped_column(String(200))
    cik: Mapped[str | None] = mapped_column(String(20))
    insider_name: Mapped[str | None] = mapped_column(String(200))
    insider_title: Mapped[str | None] = mapped_column(String(200))
    transaction_date: Mapped[date | None] = mapped_column(Date)
    filing_date: Mapped[date | None] = mapped_column(Date)
    transaction_type: Mapped[str | None] = mapped_column(String(20))
    shares: Mapped[float | None] = mapped_column(Numeric(20, 4))
    price_per_share: Mapped[float | None] = mapped_column(Numeric(16, 4))
    total_value: Mapped[float | None] = mapped_column(Numeric(20, 2))
    shares_owned_after: Mapped[float | None] = mapped_column(Numeric(20, 4))
    is_derivative: Mapped[bool] = mapped_column(Boolean, default=False)
    form4_url: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<InsiderTrade(ticker={self.ticker!r}, insider={self.insider_name!r}, "
            f"type={self.transaction_type!r}, date={self.transaction_date}, "
            f"shares={self.shares})>"
        )


class InsiderCluster(Base):
    """Cluster detection: multiple insiders buying the same stock in a window."""

    __tablename__ = "insider_clusters"
    __table_args__ = (
        UniqueConstraint(
            "ticker", "cluster_start",
            name="uq_insider_cluster_ticker_start",
        ),
        Index("idx_insider_clusters_ticker", "ticker"),
        {"schema": "signals"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(String(20))
    cluster_start: Mapped[date | None] = mapped_column(Date)
    cluster_end: Mapped[date | None] = mapped_column(Date)
    n_insiders: Mapped[int | None] = mapped_column()
    n_buys: Mapped[int | None] = mapped_column()
    n_sells: Mapped[int | None] = mapped_column()
    total_buy_value: Mapped[float | None] = mapped_column(Numeric(20, 2))
    total_sell_value: Mapped[float | None] = mapped_column(Numeric(20, 2))
    cluster_score: Mapped[float | None] = mapped_column(Numeric(10, 4))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<InsiderCluster(ticker={self.ticker!r}, "
            f"start={self.cluster_start}, end={self.cluster_end}, "
            f"n_insiders={self.n_insiders}, score={self.cluster_score})>"
        )
