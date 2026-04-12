"""ORM model for the signals.universe table.

The universe table holds every security (stock, ETF) that the system
tracks. Entries are added automatically when new signals appear
(e.g. a ticker shows up in ARK holdings or SEC filings) or manually
via the init_universe script.

Once added, tickers are never deleted – only marked as inactive.
"""

from datetime import date

from sqlalchemy import Boolean, Date, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class Universe(Base):
    """A single security in the tracking universe."""

    __tablename__ = "universe"

    # Primary key
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)

    # Identification
    company_name: Mapped[str | None] = mapped_column(String(200))
    cusip: Mapped[str | None] = mapped_column(String(20))
    isin: Mapped[str | None] = mapped_column(String(20))

    # Market info
    exchange: Mapped[str | None] = mapped_column(String(20))
    currency: Mapped[str | None] = mapped_column(String(3))
    country: Mapped[str | None] = mapped_column(String(2))
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(100))

    # Lifecycle
    added_date: Mapped[date] = mapped_column(Date, nullable=False)
    added_by: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[date | None] = mapped_column(Date)

    # Index membership (e.g. ["sp500", "nasdaq100"])
    index_membership: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(20)), default=None
    )

    # Flexible storage for additional data
    metadata_json: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, default=None
    )

    # Indexes
    __table_args__ = (
        Index("idx_universe_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Universe(ticker='{self.ticker}', name='{self.company_name}', active={self.is_active})>"
