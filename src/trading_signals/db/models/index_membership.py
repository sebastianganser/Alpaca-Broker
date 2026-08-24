"""ORM model for the signals.index_membership table.

Tracks which tickers were members of which index (S&P 500, Nasdaq 100)
over time. Each row represents a time interval [valid_from, valid_to)
during which a ticker was a member.

Used to prevent survivorship bias: when computing features for a
historical date, only tickers that were in the index on that date
are included in cross-sectional calculations.

valid_to = NULL means the ticker is currently a member.
"""

from datetime import date

from sqlalchemy import BigInteger, Date, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class IndexMembership(Base):
    """A membership interval for a ticker in a specific index."""

    __tablename__ = "index_membership"

    # Primary key
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Membership definition
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    index_name: Mapped[str] = mapped_column(String(20), nullable=False)  # 'sp500', 'nasdaq100'

    # Validity interval [valid_from, valid_to)
    # valid_to = NULL means currently active member
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Change metadata
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 'S&P 500 constituent change'
    replaced_by: Mapped[str | None] = mapped_column(String(20), nullable=True)  # Successor ticker on removal
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # 'wikipedia', 'github_csv', 'manual'

    __table_args__ = (
        UniqueConstraint("ticker", "index_name", "valid_from", name="uq_membership_ticker_index_from"),
        Index("ix_membership_lookup", "index_name", "valid_from", "valid_to"),
        Index("ix_membership_ticker", "ticker"),
    )

    def __repr__(self) -> str:
        to_str = self.valid_to.isoformat() if self.valid_to else "present"
        return (
            f"<IndexMembership(ticker='{self.ticker}', index='{self.index_name}', "
            f"{self.valid_from.isoformat()} → {to_str})>"
        )
