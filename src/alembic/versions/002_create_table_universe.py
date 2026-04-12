"""Create universe table.

Revision ID: 002
Revises: 001
Create Date: 2026-04-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "universe",
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("company_name", sa.String(200), nullable=True),
        sa.Column("cusip", sa.String(20), nullable=True),
        sa.Column("isin", sa.String(20), nullable=True),
        sa.Column("exchange", sa.String(20), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("added_date", sa.Date(), nullable=False),
        sa.Column("added_by", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("last_seen", sa.Date(), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        schema="signals",
    )
    op.create_index(
        "idx_universe_active",
        "universe",
        ["is_active"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_index("idx_universe_active", table_name="universe", schema="signals")
    op.drop_table("universe", schema="signals")
