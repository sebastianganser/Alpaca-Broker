"""Drop the barrier trade label columns from feature_snapshots.

Revision ID: 041
Revises: 040
Create Date: 2026-10-30

Migration 033 added ``return_barrier_14d`` / ``barrier_outcome`` /
``barrier_ambiguous`` for the fixed short-term trade (entry ``open(d+1)``, take
profit +1 %, stop -2 %, time stop 14 sessions). Sebastian clarified on
2026-10-17 that Alpaca-Broker forecasts rising/falling tickers but does not
define strategies or exit rules (a separate bot will trade), and the plan
``docs/2026-10-17_Plan_Strategieneutral.md`` (step 6) decided to delete the
label together with its tools (``analysis/barrier.py``, ``analysis/hit_model.py``,
``backtest_barrier.py``, ``walkforward_hit_model.py``, the barrier mode of the
event study). See "Strategy-Neutral Rework Approved" in
``docs/DECISIONS_FEATURES.md``. The git tag ``pre-neutral`` keeps the old code
reachable.

The three columns are derived data: they are computed from ``prices_daily`` by
``derived/target_backfill.py`` and can be recomputed at any time with the code
of the tag ``pre-neutral``; no data backup is taken. The forward returns
``return_1d/5d/20d/60d`` are not touched.

033 itself is not edited (it is applied in production); this migration drops
the columns with ``DROP COLUMN IF EXISTS``. The downgrade re-adds them exactly
as 033 defined them (``ADD COLUMN IF NOT EXISTS``, idempotent); the values
stay NULL, because the label can only be recomputed with the old code.
"""

from alembic import op

revision = "041"
down_revision = "040"

TABLE = "signals.feature_snapshots"

#: (column, SQL type) - as defined by migration 033, all nullable.
COLUMNS = (
    ("return_barrier_14d", "NUMERIC(10, 6)"),
    ("barrier_outcome", "SMALLINT"),
    ("barrier_ambiguous", "BOOLEAN"),
)


def upgrade() -> None:
    for name, _ in COLUMNS:
        op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {name}")


def downgrade() -> None:
    for name, sql_type in COLUMNS:
        op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {name} {sql_type}")
