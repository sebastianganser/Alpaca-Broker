"""Create prices_daily and collection_log tables.

Revision ID: 003
Revises: 002
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # prices_daily – daily OHLCV data
    op.create_table(
        "prices_daily",
        sa.Column("ticker", sa.String(20), sa.ForeignKey("signals.universe.ticker"), primary_key=True),
        sa.Column("trade_date", sa.Date(), primary_key=True),
        sa.Column("open", sa.Numeric(16, 4)),
        sa.Column("high", sa.Numeric(16, 4)),
        sa.Column("low", sa.Numeric(16, 4)),
        sa.Column("close", sa.Numeric(16, 4)),
        sa.Column("adj_close", sa.Numeric(16, 4)),
        sa.Column("volume", sa.BigInteger()),
        sa.Column("source", sa.String(50), server_default="yfinance"),
        sa.Column("is_extrapolated", sa.Boolean(), server_default="false"),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        schema="signals",
    )
    op.create_index(
        "idx_prices_date",
        "prices_daily",
        ["trade_date"],
        schema="signals",
    )
    # Partial index: only extrapolated rows (for quick filtering)
    op.execute(
        "CREATE INDEX idx_prices_extrapolated ON signals.prices_daily(is_extrapolated) "
        "WHERE is_extrapolated = TRUE"
    )

    # collection_log – audit trail for collector runs
    op.create_table(
        "collection_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("collector_name", sa.String(100)),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("status", sa.String(20)),
        sa.Column("records_fetched", sa.Integer()),
        sa.Column("records_written", sa.Integer()),
        sa.Column("gaps_detected", sa.Integer(), server_default="0"),
        sa.Column("gaps_repaired", sa.Integer(), server_default="0"),
        sa.Column("gaps_extrapolated", sa.Integer(), server_default="0"),
        sa.Column("errors", JSONB()),
        sa.Column("notes", sa.Text()),
        schema="signals",
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS signals.idx_prices_extrapolated")
    op.drop_index("idx_prices_date", table_name="prices_daily", schema="signals")
    op.drop_table("collection_log", schema="signals")
    op.drop_table("prices_daily", schema="signals")
