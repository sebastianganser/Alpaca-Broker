"""Short-interest (FINRA settlement) feature columns on feature_snapshots.

Revision ID: 036
Revises: 035
Create Date: 2026-10-16

Adds ``short_interest_days_to_cover`` and ``short_interest_change_1m``,
computed from ``signals.short_interest`` with the availability rules of
``derived/short_interest_features.py`` (settlement date usable only 9 NYSE
sessions later, max age 40 days). Nightly values come from the feature
pipeline; history is filled by
``scripts/repair/backfill_short_interest_features.py``.
"""

import sqlalchemy as sa

from alembic import op

revision = "036"
down_revision = "035"

SCHEMA = "signals"
TABLE = "feature_snapshots"

COLUMNS = (
    "short_interest_days_to_cover",
    "short_interest_change_1m",
)


def upgrade() -> None:
    for col in COLUMNS:
        op.add_column(TABLE, sa.Column(col, sa.Numeric(10, 4)), schema=SCHEMA)


def downgrade() -> None:
    for col in reversed(COLUMNS):
        op.drop_column(TABLE, col, schema=SCHEMA)
