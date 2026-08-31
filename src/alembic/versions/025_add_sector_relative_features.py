"""025 – Add sector-relative feature columns (Sprint 9.5b B4).

Revision ID: 025
Revises: 024
"""

import sqlalchemy as sa
from alembic import op

revision = "025"
down_revision = "024"


def upgrade() -> None:
    new_columns = [
        ("sector_relative_return_20d", sa.Numeric(10, 6)),
        ("sector_relative_momentum", sa.Numeric(10, 6)),
    ]
    for col_name, col_type in new_columns:
        op.add_column(
            "feature_snapshots",
            sa.Column(col_name, col_type, nullable=True),
            schema="signals",
        )


def downgrade() -> None:
    for col_name in ["sector_relative_momentum", "sector_relative_return_20d"]:
        op.drop_column("feature_snapshots", col_name, schema="signals")
