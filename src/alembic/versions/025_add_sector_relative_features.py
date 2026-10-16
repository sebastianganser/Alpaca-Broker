"""025 – Add sector-relative feature columns (Sprint 9.5b B4).

Revision ID: 025
Revises: 024

History note: migration 024 was edited after production had already applied
it (commit 81446a4 added these two columns to its feature_snapshots list), so
025 was added to create them on the production database. A fresh database
replaying 024 -> 025 therefore already has the columns when 025 runs.
``upgrade()`` is idempotent (``ADD COLUMN IF NOT EXISTS``) so both histories
replay cleanly. ``downgrade()`` is a no-op because 024's downgrade drops the
columns.
"""

from alembic import op

revision = "025"
down_revision = "024"


def upgrade() -> None:
    # NUMERIC(10, 6), nullable – identical to the former op.add_column call.
    for col_name in ("sector_relative_return_20d", "sector_relative_momentum"):
        op.execute(
            f"ALTER TABLE signals.feature_snapshots "
            f"ADD COLUMN IF NOT EXISTS {col_name} NUMERIC(10, 6)"
        )


def downgrade() -> None:
    # No-op: the columns are owned by 024 (its downgrade drops them). Dropping
    # them here would make 024's downgrade fail on a fresh database.
    pass
