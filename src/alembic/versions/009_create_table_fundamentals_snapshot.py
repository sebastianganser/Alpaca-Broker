"""Create fundamentals_snapshot table.

Revision ID: 009
Revises: 008
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # fundamentals_snapshot – weekly snapshot of key financial metrics (raw layer)
    op.create_table(
        "fundamentals_snapshot",
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("snapshot_date", sa.Date(), primary_key=True),
        sa.Column("market_cap", sa.Numeric(24, 2)),
        sa.Column("pe_ratio", sa.Numeric(16, 4)),
        sa.Column("forward_pe", sa.Numeric(16, 4)),
        sa.Column("ps_ratio", sa.Numeric(16, 4)),
        sa.Column("pb_ratio", sa.Numeric(16, 4)),
        sa.Column("ev_ebitda", sa.Numeric(16, 4)),
        sa.Column("profit_margin", sa.Numeric(10, 6)),
        sa.Column("operating_margin", sa.Numeric(10, 6)),
        sa.Column("return_on_equity", sa.Numeric(10, 6)),
        sa.Column("revenue_ttm", sa.Numeric(20, 2)),
        sa.Column("revenue_growth_yoy", sa.Numeric(10, 6)),
        sa.Column("eps_ttm", sa.Numeric(16, 4)),
        sa.Column("eps_growth_yoy", sa.Numeric(10, 6)),
        sa.Column("debt_to_equity", sa.Numeric(16, 4)),
        sa.Column("current_ratio", sa.Numeric(16, 4)),
        sa.Column("dividend_yield", sa.Numeric(10, 6)),
        sa.Column("beta", sa.Numeric(10, 4)),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        schema="signals",
    )


def downgrade() -> None:
    op.drop_table("fundamentals_snapshot", schema="signals")
