"""Create ark_holdings and ark_deltas tables.

Revision ID: 004
Revises: 003
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ark_holdings – daily snapshots of ARK ETF positions
    op.create_table(
        "ark_holdings",
        sa.Column("snapshot_date", sa.Date(), primary_key=True),
        sa.Column("etf_ticker", sa.String(10), primary_key=True),
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("company_name", sa.String(200)),
        sa.Column("cusip", sa.String(20)),
        sa.Column("shares", sa.Numeric(20, 4)),
        sa.Column("market_value", sa.Numeric(20, 2)),
        sa.Column("weight_pct", sa.Numeric(8, 4)),
        sa.Column("weight_rank", sa.Integer()),
        sa.Column("share_price", sa.Numeric(16, 4)),
        sa.Column("source", sa.String(50), server_default="arkfunds.io"),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        schema="signals",
    )
    op.create_index(
        "idx_ark_ticker",
        "ark_holdings",
        ["ticker"],
        schema="signals",
    )
    op.create_index(
        "idx_ark_date",
        "ark_holdings",
        ["snapshot_date"],
        schema="signals",
    )

    # ark_deltas – derived daily changes
    op.create_table(
        "ark_deltas",
        sa.Column("delta_date", sa.Date(), primary_key=True),
        sa.Column("etf_ticker", sa.String(10), primary_key=True),
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("delta_type", sa.String(20), nullable=False),
        sa.Column("shares_prev", sa.Numeric(20, 4)),
        sa.Column("shares_curr", sa.Numeric(20, 4)),
        sa.Column("shares_delta", sa.Numeric(20, 4)),
        sa.Column("weight_prev", sa.Numeric(8, 4)),
        sa.Column("weight_curr", sa.Numeric(8, 4)),
        sa.Column("weight_delta", sa.Numeric(8, 4)),
        sa.Column("computed_at", sa.DateTime(), server_default=sa.func.now()),
        schema="signals",
    )
    op.create_index(
        "idx_ark_deltas_ticker",
        "ark_deltas",
        ["ticker"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_index("idx_ark_deltas_ticker", table_name="ark_deltas", schema="signals")
    op.drop_table("ark_deltas", schema="signals")
    op.drop_index("idx_ark_date", table_name="ark_holdings", schema="signals")
    op.drop_index("idx_ark_ticker", table_name="ark_holdings", schema="signals")
    op.drop_table("ark_holdings", schema="signals")
