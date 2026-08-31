"""026 – Create options_iv_snapshot table (Sprint 9.5b D3).

Stores daily implied volatility data per ticker from Alpaca Options API.
Used for IV-Rank, Skew, Term Structure features (needs ~1 year buildup).

Revision ID: 026
Revises: 025
"""

import sqlalchemy as sa
from alembic import op

revision = "026"
down_revision = "025"


def upgrade() -> None:
    op.create_table(
        "options_iv_snapshot",
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("atm_iv_30d", sa.Numeric(8, 4)),
        sa.Column("atm_iv_60d", sa.Numeric(8, 4)),
        sa.Column("skew_25d", sa.Numeric(8, 4)),
        sa.Column("term_slope", sa.Numeric(8, 4)),
        sa.Column("total_oi_call", sa.BigInteger),
        sa.Column("total_oi_put", sa.BigInteger),
        sa.Column("put_call_oi", sa.Numeric(8, 4)),
        sa.PrimaryKeyConstraint("ticker", "snapshot_date"),
        schema="signals",
    )
    op.create_index(
        "idx_options_iv_ticker_date",
        "options_iv_snapshot",
        ["ticker", "snapshot_date"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_options_iv_ticker_date",
        table_name="options_iv_snapshot",
        schema="signals",
    )
    op.drop_table("options_iv_snapshot", schema="signals")
