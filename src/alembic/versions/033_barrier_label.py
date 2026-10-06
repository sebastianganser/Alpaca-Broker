"""Barrier trade label columns on feature_snapshots.

Revision ID: 033
Revises: 032
Create Date: 2026-10-06

Adds the label of the actual short-term trade (concept
``docs/2026-10-06_Konzept_Kurzfrist_Kandidaten.md``): entry open(d+1),
take profit +1 %, stop −2 %, time stop 14 sessions. Filled by
``derived/target_backfill.py`` (the nightly run back-fills all rows whose
label is still NULL).
"""

import sqlalchemy as sa

from alembic import op

revision = "033"
down_revision = "032"

SCHEMA = "signals"
TABLE = "feature_snapshots"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("return_barrier_14d", sa.Numeric(10, 6)), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("barrier_outcome", sa.SmallInteger()), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("barrier_ambiguous", sa.Boolean()), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column(TABLE, "barrier_ambiguous", schema=SCHEMA)
    op.drop_column(TABLE, "barrier_outcome", schema=SCHEMA)
    op.drop_column(TABLE, "return_barrier_14d", schema=SCHEMA)
