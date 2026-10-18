"""SEC filings, filing texts, quarterly financials; more yfinance fundamentals.

Revision ID: 040
Revises: 039
Create Date: 2026-10-18

Three new data feeds for the Wall-Street-style assessment (docs/DATA_SOURCES.md
sections 13 and 14, DECISIONS_DATA 2026-10-18):

* ``sec_filings`` - one row per SEC filing (accession number) of a universe
  company: 8-K (+ item codes), 10-Q/10-K, 144, 13D/13G, S-1/S-3, DEF 14A, ...
  Written by the daily ``sec_filings`` collector (EDGAR submissions API).
  ``acceptance_datetime`` is the point-in-time key.
* ``sec_filing_texts`` - cleaned excerpt (first ~3,000 characters) of the
  press release (EX-99.x) or of the body of an 8-K with a material event item;
  child of ``sec_filings`` (``ON DELETE CASCADE``).
* ``financials_quarterly`` - standardised quarterly financials from the XBRL
  ``companyfacts`` API (weekly ``sec_financials`` collector), point-in-time key
  ``filed``.
* ``fundamentals_snapshot`` - twelve more nullable columns taken from the same
  yfinance ``info`` call as the existing fields (shares, float, holder shares,
  short percent of float, free cash flow, cash, debt, gross margin, analyst
  consensus).

The downgrade drops the new columns and tables again.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op

revision = "040"
down_revision = "039"

SCHEMA = "signals"

#: (name, type) of the new ``fundamentals_snapshot`` columns
FUNDAMENTALS_COLUMNS = (
    ("shares_outstanding", sa.Numeric(20, 2)),
    ("float_shares", sa.Numeric(20, 2)),
    ("held_percent_institutions", sa.Numeric(10, 6)),
    ("held_percent_insiders", sa.Numeric(10, 6)),
    ("short_percent_of_float", sa.Numeric(10, 6)),
    ("free_cashflow", sa.Numeric(24, 2)),
    ("total_cash", sa.Numeric(24, 2)),
    ("total_debt", sa.Numeric(24, 2)),
    ("gross_margins", sa.Numeric(10, 6)),
    ("recommendation_mean", sa.Numeric(6, 3)),
    ("recommendation_key", sa.String(20)),
    ("number_of_analyst_opinions", sa.SmallInteger()),
)


def upgrade() -> None:
    op.create_table(
        "sec_filings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("tickers", ARRAY(sa.String(20)), nullable=False),
        sa.Column("accession_number", sa.String(25), nullable=False),
        sa.Column("form", sa.String(20), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("acceptance_datetime", sa.DateTime(timezone=True)),
        sa.Column("acceptance_raw", sa.String(32)),
        sa.Column("report_date", sa.Date()),
        sa.Column("items", ARRAY(sa.Text())),
        sa.Column("primary_document", sa.String(255)),
        sa.Column("url", sa.Text()),
        sa.Column("is_amendment", sa.Boolean(), nullable=False),
        sa.Column("texts_fetched_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("accession_number", name="uq_sec_filings_accession_number"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sec_filings_ticker_filing_date",
        "sec_filings",
        ["ticker", "filing_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sec_filings_filing_date", "sec_filings", ["filing_date"], schema=SCHEMA
    )
    op.create_index(
        "ix_sec_filings_cik_filing_date",
        "sec_filings",
        ["cik", "filing_date"],
        schema=SCHEMA,
    )

    op.create_table(
        "sec_filing_texts",
        sa.Column(
            "accession_number",
            sa.String(25),
            sa.ForeignKey(f"{SCHEMA}.sec_filings.accession_number", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("exhibit", sa.String(20), primary_key=True),
        sa.Column("filename", sa.String(255)),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("full_length", sa.Integer()),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "financials_quarterly",
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("period_end", sa.Date(), primary_key=True),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("fiscal_year", sa.SmallInteger()),
        sa.Column("fiscal_period", sa.String(2)),
        sa.Column("form", sa.String(12)),
        sa.Column("filed", sa.Date(), nullable=False),
        sa.Column("accession_number", sa.String(25)),
        sa.Column("revenue", sa.Numeric(24, 2)),
        sa.Column("gross_profit", sa.Numeric(24, 2)),
        sa.Column("operating_income", sa.Numeric(24, 2)),
        sa.Column("net_income", sa.Numeric(24, 2)),
        sa.Column("eps_diluted", sa.Numeric(14, 4)),
        sa.Column("operating_cash_flow", sa.Numeric(24, 2)),
        sa.Column("capex", sa.Numeric(24, 2)),
        sa.Column("free_cash_flow", sa.Numeric(24, 2)),
        sa.Column("cash_and_equivalents", sa.Numeric(24, 2)),
        sa.Column("total_debt", sa.Numeric(24, 2)),
        sa.Column("shares_outstanding", sa.Numeric(24, 2)),
        sa.Column("raw_tags", JSONB()),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_financials_quarterly_period_end",
        "financials_quarterly",
        ["period_end"],
        schema=SCHEMA,
    )

    for name, type_ in FUNDAMENTALS_COLUMNS:
        op.add_column("fundamentals_snapshot", sa.Column(name, type_), schema=SCHEMA)


def downgrade() -> None:
    for name, _ in reversed(FUNDAMENTALS_COLUMNS):
        op.drop_column("fundamentals_snapshot", name, schema=SCHEMA)
    op.drop_table("financials_quarterly", schema=SCHEMA)
    op.drop_table("sec_filing_texts", schema=SCHEMA)
    op.drop_table("sec_filings", schema=SCHEMA)
