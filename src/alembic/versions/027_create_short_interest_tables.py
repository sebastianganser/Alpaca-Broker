"""027 – Create short_interest tables and feature snapshot columns (Sprint 9.5c B5).

Creates:
- short_volume (daily)
- short_interest (bi-monthly)
Adds columns to feature_snapshots:
- short_volume_ratio_5d
- short_volume_ratio_20d
- short_volume_change_20d

Revision ID: 027
Revises: 026
"""

import sqlalchemy as sa
from alembic import op

revision = "027"
down_revision = "026"


def upgrade() -> None:
    # 1. Create short_volume
    op.create_table(
        "short_volume",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("short_volume", sa.BigInteger),
        sa.Column("total_volume", sa.BigInteger),
        sa.Column("short_volume_ratio", sa.Numeric(6, 4)),
        sa.Column("source", sa.String(50), server_default="massive"),
        sa.Column("fetched_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("ticker", "trade_date", name="uq_short_volume_dedup"),
        schema="signals",
    )
    op.create_index(
        "idx_short_volume_ticker",
        "short_volume",
        ["ticker"],
        schema="signals",
    )
    op.create_index(
        "idx_short_volume_date",
        "short_volume",
        ["trade_date"],
        schema="signals",
    )

    # 2. Create short_interest
    op.create_table(
        "short_interest",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("settlement_date", sa.Date, nullable=False),
        sa.Column("short_interest", sa.BigInteger),
        sa.Column("avg_daily_volume", sa.BigInteger),
        sa.Column("days_to_cover", sa.Numeric(10, 4)),
        sa.Column("source", sa.String(50), server_default="massive"),
        sa.Column("fetched_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("ticker", "settlement_date", "source", name="uq_short_interest_dedup"),
        schema="signals",
    )
    op.create_index(
        "idx_short_interest_ticker",
        "short_interest",
        ["ticker"],
        schema="signals",
    )

    # 3. Add columns to feature_snapshots
    op.add_column("feature_snapshots", sa.Column("short_volume_ratio_5d", sa.Numeric(10, 4)), schema="signals")
    op.add_column("feature_snapshots", sa.Column("short_volume_ratio_20d", sa.Numeric(10, 4)), schema="signals")
    op.add_column("feature_snapshots", sa.Column("short_volume_change_20d", sa.Numeric(10, 4)), schema="signals")


def downgrade() -> None:
    op.drop_column("feature_snapshots", "short_volume_change_20d", schema="signals")
    op.drop_column("feature_snapshots", "short_volume_ratio_20d", schema="signals")
    op.drop_column("feature_snapshots", "short_volume_ratio_5d", schema="signals")

    op.drop_index("idx_short_interest_ticker", table_name="short_interest", schema="signals")
    op.drop_table("short_interest", schema="signals")

    op.drop_index("idx_short_volume_date", table_name="short_volume", schema="signals")
    op.drop_index("idx_short_volume_ticker", table_name="short_volume", schema="signals")
    op.drop_table("short_volume", schema="signals")
