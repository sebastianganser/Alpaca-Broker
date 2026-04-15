"""Add log_lines JSONB column to collection_log.

Stores captured WARNING/ERROR/INFO log lines from each collector run,
enabling the UI to display detailed log output for debugging.

Revision ID: 014
Revises: 013
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collection_log",
        sa.Column("log_lines", JSONB, nullable=True),
        schema="signals",
    )


def downgrade() -> None:
    op.drop_column("collection_log", "log_lines", schema="signals")
