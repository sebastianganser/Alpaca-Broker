"""Trailing-stop trade label columns on feature_snapshots.

Revision ID: 037
Revises: 036
Create Date: 2026-10-17

Adds the label of the medium-term trend trade (decision 2026-10-17 in
``docs/DECISIONS_FEATURES.md``): entry open(d+1), trailing stop at
3 x ATR(14) below the high-water mark, time stop after 60 sessions. Filled
by ``derived/target_backfill.py`` (the nightly run back-fills all rows whose
label is still NULL; ``scripts/repair/features_rebuild.py --skip-ta
--skip-clusters --skip-features`` recomputes all of them).

``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS`` keep the migration
idempotent, so a replay on a database that already has the columns is safe.
"""

from alembic import op

revision = "037"
down_revision = "036"

TABLE = "signals.feature_snapshots"

#: (column, SQL type) – mirrored by ``FeatureSnapshot`` in db/models/features.py.
COLUMNS = (
    ("return_trail_60d", "NUMERIC(10, 6)"),
    ("trail_exit_day", "SMALLINT"),
    ("trail_stopped", "BOOLEAN"),
)


def upgrade() -> None:
    for name, sql_type in COLUMNS:
        op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {name} {sql_type}")


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {name}")
