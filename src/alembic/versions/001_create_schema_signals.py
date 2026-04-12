"""Create signals schema.

Revision ID: 001
Revises: None
Create Date: 2026-04-12
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS signals")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS signals CASCADE")
