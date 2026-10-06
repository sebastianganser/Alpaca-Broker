"""Short-term price feature columns on feature_snapshots.

Revision ID: 034
Revises: 033
Create Date: 2026-10-06

Adds nine ``st_*`` features for the 1–14 session horizon (concept
``docs/2026-10-06_Konzept_Kurzfrist_Kandidaten.md`` §7.1, step A2).
Computed by ``derived/short_term_features.py``; history is filled by
``scripts/repair/backfill_short_term_features.py``.
"""

import sqlalchemy as sa

from alembic import op

revision = "034"
down_revision = "033"

SCHEMA = "signals"
TABLE = "feature_snapshots"

COLUMNS = (
    "st_return_1d",
    "st_return_5d",
    "st_gap",
    "st_close_location",
    "st_dist_52w_high",
    "st_rsi_2",
    "st_bollinger_pctb",
    "st_signed_volume_shock",
    "st_earnings_reaction",
)


def upgrade() -> None:
    for col in COLUMNS:
        op.add_column(TABLE, sa.Column(col, sa.Numeric(12, 6)), schema=SCHEMA)


def downgrade() -> None:
    for col in reversed(COLUMNS):
        op.drop_column(TABLE, col, schema=SCHEMA)
