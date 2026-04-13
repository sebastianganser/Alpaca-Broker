"""Create technical_indicators table.

Revision ID: 012
Revises: 011
"""

from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # technical_indicators – derived TA indicators per ticker per day
    op.create_table(
        "technical_indicators",
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("trade_date", sa.Date(), primary_key=True),
        # Moving Averages
        sa.Column("sma_20", sa.Numeric(16, 4)),
        sa.Column("sma_50", sa.Numeric(16, 4)),
        sa.Column("sma_200", sa.Numeric(16, 4)),
        sa.Column("ema_12", sa.Numeric(16, 4)),
        sa.Column("ema_26", sa.Numeric(16, 4)),
        # Momentum
        sa.Column("rsi_14", sa.Numeric(10, 4)),
        sa.Column("macd", sa.Numeric(16, 4)),
        sa.Column("macd_signal", sa.Numeric(16, 4)),
        sa.Column("macd_histogram", sa.Numeric(16, 4)),
        # Volatility
        sa.Column("bollinger_upper", sa.Numeric(16, 4)),
        sa.Column("bollinger_lower", sa.Numeric(16, 4)),
        sa.Column("atr_14", sa.Numeric(16, 4)),
        # Volume
        sa.Column("volume_sma_20", sa.Numeric(20, 2)),
        # Relative performance
        sa.Column("relative_strength_spy", sa.Numeric(10, 4)),
        schema="signals",
    )

    # Index for time-based queries (e.g., "all indicators for today")
    op.create_index(
        "idx_technical_indicators_date",
        "technical_indicators",
        ["trade_date"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_technical_indicators_date",
        table_name="technical_indicators",
        schema="signals",
    )
    op.drop_table("technical_indicators", schema="signals")
