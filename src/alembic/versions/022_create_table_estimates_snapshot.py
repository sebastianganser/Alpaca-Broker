"""Create estimates_snapshot table.

Sprint 9.5a: Analyst consensus estimates with rolling 90-day revision window.
Most time-critical data source — every day of delay permanently loses
one day of revision history from Yahoo's rolling window.

Revision ID: 022
Revises: 021
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "estimates_snapshot",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("period", sa.String(10), nullable=False),
        # EPS Consensus
        sa.Column("eps_avg", sa.Numeric(16, 4)),
        sa.Column("eps_low", sa.Numeric(16, 4)),
        sa.Column("eps_high", sa.Numeric(16, 4)),
        sa.Column("eps_n_analysts", sa.Integer()),
        sa.Column("eps_year_ago", sa.Numeric(16, 4)),
        sa.Column("eps_growth", sa.Numeric(10, 6)),
        # EPS Trend (rolling 90-day window)
        sa.Column("eps_current", sa.Numeric(16, 4)),
        sa.Column("eps_7d_ago", sa.Numeric(16, 4)),
        sa.Column("eps_30d_ago", sa.Numeric(16, 4)),
        sa.Column("eps_60d_ago", sa.Numeric(16, 4)),
        sa.Column("eps_90d_ago", sa.Numeric(16, 4)),
        # Revision counts
        sa.Column("rev_up_7d", sa.Integer()),
        sa.Column("rev_up_30d", sa.Integer()),
        sa.Column("rev_down_7d", sa.Integer()),
        sa.Column("rev_down_30d", sa.Integer()),
        # Revenue Consensus
        sa.Column("revenue_avg", sa.Numeric(20, 2)),
        sa.Column("revenue_low", sa.Numeric(20, 2)),
        sa.Column("revenue_high", sa.Numeric(20, 2)),
        sa.Column("revenue_n_analysts", sa.Integer()),
        sa.Column("revenue_year_ago", sa.Numeric(20, 2)),
        sa.Column("revenue_growth", sa.Numeric(10, 6)),
        # Metadata
        sa.Column("source", sa.String(50), nullable=False, server_default="yfinance"),
        sa.Column("raw", JSONB()),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        # Constraints
        sa.PrimaryKeyConstraint("id", name="pk_estimates_snapshot"),
        sa.UniqueConstraint(
            "ticker", "as_of", "period", "source",
            name="uq_estimates_snapshot_dedup",
        ),
        schema="signals",
    )

    op.create_index(
        "idx_estimates_ticker",
        "estimates_snapshot",
        ["ticker"],
        schema="signals",
    )
    op.create_index(
        "idx_estimates_as_of",
        "estimates_snapshot",
        ["as_of"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_index("idx_estimates_as_of", table_name="estimates_snapshot", schema="signals")
    op.drop_index("idx_estimates_ticker", table_name="estimates_snapshot", schema="signals")
    op.drop_table("estimates_snapshot", schema="signals")
