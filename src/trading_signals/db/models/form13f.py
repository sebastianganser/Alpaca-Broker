"""ORM model for SEC Form 13F institutional holdings.

Form13FHolding: Raw layer – quarterly holdings of institutional investors
               ($100M+ AUM) from SEC 13F-HR filings.

Key: one row per (filer, report period, CUSIP, put/call). Multiple infotable
lines for the same key (different investment discretion / other managers)
are summed by the collector. ``put_call`` is ``'SH'`` for plain share/
principal positions and ``'PUT'``/``'CALL'`` for option positions – consumers
counting holders should filter ``put_call = 'SH'``.
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class Form13FHolding(Base):
    """A single holding from an institutional investor's 13F-HR filing."""

    __tablename__ = "form13f_holdings"
    __table_args__ = (
        UniqueConstraint(
            "filer_cik", "report_period", "cusip", "put_call",
            name="uq_13f_holding_key",
        ),
        Index("idx_13f_ticker", "ticker"),
        Index("idx_13f_filer_period", "filer_cik", "report_period"),
        {"schema": "signals"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    filer_name: Mapped[str | None] = mapped_column(String(200))
    filer_cik: Mapped[str | None] = mapped_column(String(20))
    report_period: Mapped[date | None] = mapped_column(Date)
    filing_date: Mapped[date | None] = mapped_column(Date)
    ticker: Mapped[str | None] = mapped_column(String(20))
    cusip: Mapped[str | None] = mapped_column(String(20))
    shares: Mapped[float | None] = mapped_column(Numeric(20, 4))
    # Always whole US dollars (pre-2023 filings reported $ thousands and
    # are scaled by the collector).
    market_value: Mapped[float | None] = mapped_column(Numeric(20, 2))
    put_call: Mapped[str] = mapped_column(
        String(10), nullable=False, default="SH", server_default=text("'SH'")
    )
    # Filing provenance (accession of the filing the row came from;
    # NULL for rows written before migration 029).
    accession_number: Mapped[str | None] = mapped_column(String(25))
    form_type: Mapped[str | None] = mapped_column(String(12))
    amendment_type: Mapped[str | None] = mapped_column(String(20))
    source_url: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<Form13FHolding(filer={self.filer_name!r}, "
            f"ticker={self.ticker!r}, period={self.report_period}, "
            f"shares={self.shares})>"
        )
