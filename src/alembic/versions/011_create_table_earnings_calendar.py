"""Create earnings_calendar table.

Revision ID: 011
Revises: 010
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # earnings_calendar – earnings dates with EPS estimates and surprises (raw layer)
    op.create_table(
        "earnings_calendar",
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("earnings_date", sa.Date(), primary_key=True),
        sa.Column("time_of_day", sa.String(20)),  # BMO, AMC
        sa.Column("eps_estimate", sa.Numeric(16, 4)),
        sa.Column("eps_actual", sa.Numeric(16, 4)),
        sa.Column("revenue_estimate", sa.Numeric(20, 2)),
        sa.Column("revenue_actual", sa.Numeric(20, 2)),
        sa.Column("surprise_pct", sa.Numeric(10, 4)),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        schema="signals",
    )


def downgrade() -> None:
    op.drop_table("earnings_calendar", schema="signals")
