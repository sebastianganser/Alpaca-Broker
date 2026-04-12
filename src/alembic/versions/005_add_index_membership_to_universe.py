"""Add index_membership column to universe table.

Revision ID: 005
Revises: 004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "universe",
        sa.Column(
            "index_membership",
            ARRAY(sa.String(20)),
            nullable=True,
            comment="Index memberships, e.g. {sp500, nasdaq100}",
        ),
        schema="signals",
    )


def downgrade() -> None:
    op.drop_column("universe", "index_membership", schema="signals")
