"""CUSIP → ticker lookup cache for 13F holdings.

Revision ID: 032
Revises: 031
Create Date: 2026-10-05

The 13F collector never resolved CUSIPs to tickers (``ticker`` was always
NULL), so all 13F features stayed empty. ``cusip_map`` caches the resolved
ticker per CUSIP (local sources + OpenFIGI); the collector and
``scripts/repair/form13f_resolve_tickers.py`` fill
``form13f_holdings.ticker`` from it.
"""

import sqlalchemy as sa

from alembic import op

revision = "032"
down_revision = "031"

SCHEMA = "signals"


def upgrade() -> None:
    op.create_table(
        "cusip_map",
        sa.Column("cusip", sa.String(12), primary_key=True),
        sa.Column("ticker", sa.String(20), nullable=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("exch_code", sa.String(10), nullable=True),
        sa.Column("security_type", sa.String(50), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_cusip_map_ticker", "cusip_map", ["ticker"], schema=SCHEMA
    )
    # Feature pipeline filters by (ticker, report_period, filing_date)
    op.create_index(
        "idx_13f_ticker_period",
        "form13f_holdings",
        ["ticker", "report_period"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("idx_13f_ticker_period", "form13f_holdings", schema=SCHEMA)
    op.drop_index("ix_cusip_map_ticker", "cusip_map", schema=SCHEMA)
    op.drop_table("cusip_map", schema=SCHEMA)
