"""016: Add analyst consensus price target columns to fundamentals_snapshot.

Adds target_price_low, target_price_mean, target_price_median, target_price_high
to store the analyst consensus price target range from yfinance.

Revision ID: 016
"""

from alembic import op
import sqlalchemy as sa

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fundamentals_snapshot",
        sa.Column("target_price_low", sa.Numeric(16, 4), nullable=True),
        schema="signals",
    )
    op.add_column(
        "fundamentals_snapshot",
        sa.Column("target_price_mean", sa.Numeric(16, 4), nullable=True),
        schema="signals",
    )
    op.add_column(
        "fundamentals_snapshot",
        sa.Column("target_price_median", sa.Numeric(16, 4), nullable=True),
        schema="signals",
    )
    op.add_column(
        "fundamentals_snapshot",
        sa.Column("target_price_high", sa.Numeric(16, 4), nullable=True),
        schema="signals",
    )


def downgrade() -> None:
    op.drop_column("fundamentals_snapshot", "target_price_high", schema="signals")
    op.drop_column("fundamentals_snapshot", "target_price_median", schema="signals")
    op.drop_column("fundamentals_snapshot", "target_price_mean", schema="signals")
    op.drop_column("fundamentals_snapshot", "target_price_low", schema="signals")
