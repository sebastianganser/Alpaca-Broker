"""ORM model for US politician stock trades (STOCK Act disclosures).

PoliticianTrade: Raw layer – individual transactions from congressional
                 financial disclosure filings (Senate eFD + House Clerk).
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


class PoliticianTrade(Base):
    """A single stock transaction by a US Congress member."""

    __tablename__ = "politician_trades"
    __table_args__ = (
        UniqueConstraint(
            "politician_name", "ticker", "transaction_date",
            "transaction_type", "amount_range",
            name="uq_politician_trade_dedup",
        ),
        Index("idx_politician_ticker", "ticker"),
        Index("idx_politician_date", "transaction_date"),
        {"schema": "signals"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    politician_name: Mapped[str | None] = mapped_column(String(200))
    chamber: Mapped[str | None] = mapped_column(String(20))  # 'Senate', 'House'
    party: Mapped[str | None] = mapped_column(String(20))
    state: Mapped[str | None] = mapped_column(String(2))
    ticker: Mapped[str | None] = mapped_column(String(20))
    transaction_date: Mapped[date | None] = mapped_column(Date)
    disclosure_date: Mapped[date | None] = mapped_column(Date)
    transaction_type: Mapped[str | None] = mapped_column(String(20))  # Purchase, Sale
    amount_range: Mapped[str | None] = mapped_column(String(50))
    owner: Mapped[str | None] = mapped_column(String(50))  # Self, Spouse, Joint, Child
    asset_description: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<PoliticianTrade(politician={self.politician_name!r}, "
            f"ticker={self.ticker!r}, type={self.transaction_type!r}, "
            f"date={self.transaction_date}, amount={self.amount_range!r})>"
        )
