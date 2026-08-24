"""Create index_membership table.

Sprint 9.5a A1: Survivorship Bias Prevention.
Tracks historical index membership with time intervals
to enable point-in-time universe queries.

Revision ID: 023
Revises: 022
"""

from alembic import op
import sqlalchemy as sa

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_membership",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("index_name", sa.String(20), nullable=False),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_to", sa.Date, nullable=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("replaced_by", sa.String(20), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        # Constraints
        sa.UniqueConstraint(
            "ticker", "index_name", "valid_from",
            name="uq_membership_ticker_index_from",
        ),
        schema="signals",
    )

    # Lookup index for point-in-time queries:
    #   WHERE index_name = :name AND valid_from <= :date AND (valid_to IS NULL OR valid_to > :date)
    op.create_index(
        "ix_membership_lookup",
        "index_membership",
        ["index_name", "valid_from", "valid_to"],
        schema="signals",
    )

    # Ticker lookup (for finding all memberships of a specific ticker)
    op.create_index(
        "ix_membership_ticker",
        "index_membership",
        ["ticker"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_index("ix_membership_ticker", table_name="index_membership", schema="signals")
    op.drop_index("ix_membership_lookup", table_name="index_membership", schema="signals")
    op.drop_table("index_membership", schema="signals")
