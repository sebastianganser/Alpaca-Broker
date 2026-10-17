"""Drop the trailing-stop trade label columns from feature_snapshots.

Revision ID: 038
Revises: 037
Create Date: 2026-10-17

Migration 037 added ``return_trail_60d`` / ``trail_exit_day`` /
``trail_stopped`` for a trailing-stop trend-trade label. Sebastian clarified
the same evening that Alpaca-Broker forecasts rising/falling tickers but does
not define strategies or exit rules (a separate bot will trade), so the label
is removed again (decision 2026-10-17 "Scope: Alpaca-Broker forecasts, a
separate bot trades" in ``docs/DECISIONS_FEATURES.md``). The monthly analysis
now uses the strategy-neutral ``return_60d`` as model target.

037 itself is not edited (it is applied in production); this migration drops
the columns with ``DROP COLUMN IF EXISTS``. The downgrade re-adds them exactly
as 037 defined them (``ADD COLUMN IF NOT EXISTS``, idempotent).
"""

from alembic import op

revision = "038"
down_revision = "037"

TABLE = "signals.feature_snapshots"

#: (column, SQL type) – as defined by migration 037.
COLUMNS = (
    ("return_trail_60d", "NUMERIC(10, 6)"),
    ("trail_exit_day", "SMALLINT"),
    ("trail_stopped", "BOOLEAN"),
)


def upgrade() -> None:
    for name, _ in COLUMNS:
        op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {name}")


def downgrade() -> None:
    for name, sql_type in COLUMNS:
        op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {name} {sql_type}")
