"""Create analyst_ratings table.

Revision ID: 010
Revises: 009
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # analyst_ratings – individual analyst upgrades/downgrades (raw layer)
    op.create_table(
        "analyst_ratings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ticker", sa.String(20)),
        sa.Column("firm", sa.String(200)),
        sa.Column("analyst", sa.String(200)),
        sa.Column("rating_date", sa.Date()),
        sa.Column("rating_new", sa.String(50)),
        sa.Column("rating_old", sa.String(50)),
        sa.Column("price_target_new", sa.Numeric(16, 4)),
        sa.Column("price_target_old", sa.Numeric(16, 4)),
        sa.Column("action", sa.String(50)),  # up, down, main, init, reit
        sa.Column("raw_data", JSONB()),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        schema="signals",
    )
    op.create_index(
        "idx_analyst_ticker",
        "analyst_ratings",
        ["ticker"],
        schema="signals",
    )
    op.create_index(
        "idx_analyst_rating_date",
        "analyst_ratings",
        ["rating_date"],
        schema="signals",
    )
    # Dedup constraint
    op.create_unique_constraint(
        "uq_analyst_rating_dedup",
        "analyst_ratings",
        ["ticker", "firm", "rating_date", "action"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_analyst_rating_dedup",
        "analyst_ratings",
        schema="signals",
    )
    op.drop_index(
        "idx_analyst_rating_date",
        table_name="analyst_ratings",
        schema="signals",
    )
    op.drop_index(
        "idx_analyst_ticker",
        table_name="analyst_ratings",
        schema="signals",
    )
    op.drop_table("analyst_ratings", schema="signals")
