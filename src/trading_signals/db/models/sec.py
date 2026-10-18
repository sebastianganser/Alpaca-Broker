"""ORM models of the SEC EDGAR feeds (migration 040).

Documentation: docs/DATA_SOURCES.md sections 13 and 14, docs/DATABASE.md.

* :class:`SecFiling` - one row per filing (accession number) of a universe
  company: form, filing date, acceptance time, 8-K item codes, document URL.
  Written by the daily ``sec_filings`` collector.
* :class:`SecFilingText` - cleaned plain-text excerpt (first ~3,000 characters)
  of the press release (EX-99.x) or of the body of an 8-K with a material
  event item. Child of :class:`SecFiling`.
* :class:`FinancialsQuarterly` - standardised quarterly financials built from
  the XBRL ``companyfacts`` API (weekly ``sec_financials`` collector), keyed by
  the public ``filed`` date (point-in-time).

Timestamps of these tables are plain ``timestamptz`` (aware values), like
``insider_trades.acceptance_datetime``: they are compared with the 16:00 /
09:30 New York cut-offs without any wall-clock conversion.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trading_signals.db.base import Base


class SecFiling(Base):
    """One SEC filing (form whitelist: ``collectors/sec_filings_collector.py``).

    ``ticker`` is the first (alphabetical) active universe ticker of the
    company's CIK; ``tickers`` lists all of them, so share classes that share
    a CIK (GOOG/GOOGL, FOX/FOXA, NWS/NWSA) find the filing with
    ``'GOOGL' = ANY(tickers)``. ``acceptance_datetime`` is the true instant at
    which EDGAR accepted the filing (UTC); ``acceptance_raw`` keeps the exact
    string of the submissions JSON (see ``collectors/sec_client.py``,
    ``submission_acceptance_utc``). Point-in-time: a filing is public from
    ``acceptance_datetime`` (``NULL`` -> from the end of ``filing_date``).
    """

    __tablename__ = "sec_filings"
    __table_args__ = (
        UniqueConstraint("accession_number", name="uq_sec_filings_accession_number"),
        Index("ix_sec_filings_ticker_filing_date", "ticker", "filing_date"),
        Index("ix_sec_filings_filing_date", "filing_date"),
        Index("ix_sec_filings_cik_filing_date", "cik", "filing_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(String(10), nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    tickers: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False)
    accession_number: Mapped[str] = mapped_column(String(25), nullable=False)
    form: Mapped[str] = mapped_column(String(20), nullable=False)
    filing_date: Mapped[date] = mapped_column(Date, nullable=False)
    acceptance_datetime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    acceptance_raw: Mapped[str | None] = mapped_column(String(32))
    report_date: Mapped[date | None] = mapped_column(Date)
    # 8-K item codes, e.g. {2.02,9.01}; NULL for other forms
    items: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    primary_document: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    is_amendment: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # set when the text fetch (press release / 8-K body) finished: with rows in
    # sec_filing_texts or because the filing has none. NULL = not attempted or
    # failed (retried by the next runs while the filing is inside the lookback).
    texts_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<SecFiling({self.ticker!r}, {self.form!r}, {self.filing_date}, "
            f"items={self.items}, {self.accession_number!r})>"
        )


class SecFilingText(Base):
    """Cleaned excerpt of an exhibit / 8-K body (child of :class:`SecFiling`).

    ``exhibit`` is the SEC document type (``EX-99.1``) or ``8-K`` for the body
    of the report itself; ``full_length`` is the length of the cleaned text
    before it was cut to the excerpt.
    """

    __tablename__ = "sec_filing_texts"

    accession_number: Mapped[str] = mapped_column(
        String(25),
        ForeignKey("signals.sec_filings.accession_number", ondelete="CASCADE"),
        primary_key=True,
    )
    exhibit: Mapped[str] = mapped_column(String(20), primary_key=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    full_length: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<SecFilingText({self.accession_number!r}, {self.exhibit!r}, "
            f"{len(self.excerpt or '')} chars)>"
        )


class FinancialsQuarterly(Base):
    """Standardised quarterly financials of one ticker (SEC XBRL companyfacts).

    One row per (ticker, fiscal quarter). Values are as FIRST reported (later
    restatements are ignored); ``filed`` is the date on which the last value
    of the row became public (point-in-time key: use rows with
    ``filed <= d``). Q4 = fiscal year minus 9 months, cash-flow quarters are
    differences of year-to-date values, ``raw_tags`` names the us-gaap tag of
    every figure and the figures that were derived. USD only. Tickers that
    share a CIK (share classes) get identical rows.
    """

    __tablename__ = "financials_quarterly"
    __table_args__ = (Index("ix_financials_quarterly_period_end", "period_end"),)

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    cik: Mapped[str] = mapped_column(String(10), nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(SmallInteger)
    fiscal_period: Mapped[str | None] = mapped_column(String(2))  # Q1..Q4
    form: Mapped[str | None] = mapped_column(String(12))  # 10-Q / 10-K
    filed: Mapped[date] = mapped_column(Date, nullable=False)
    accession_number: Mapped[str | None] = mapped_column(String(25))
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    gross_profit: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    operating_income: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    net_income: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    eps_diluted: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    operating_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    capex: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    free_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    cash_and_equivalents: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    total_debt: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    shares_outstanding: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    raw_tags: Mapped[dict | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<FinancialsQuarterly({self.ticker!r}, {self.period_end}, "
            f"{self.fiscal_year}{self.fiscal_period}, revenue={self.revenue})>"
        )
