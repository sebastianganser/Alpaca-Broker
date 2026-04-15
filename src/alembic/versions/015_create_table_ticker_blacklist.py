"""Create ticker_blacklist table.

Stores tickers identified as non-equity (ETFs, mutual funds, etc.)
via yfinance quoteType. Used as a learning filter to prevent
ETFs from entering the universe.

Revision ID: 015
Revises: 014
"""

from alembic import op
import sqlalchemy as sa

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticker_blacklist",
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("reason", sa.String(50), nullable=False),
        sa.Column("quote_type", sa.String(20)),
        sa.Column("source", sa.String(50)),
        sa.Column("detected_at", sa.DateTime, server_default=sa.func.now()),
        schema="signals",
    )


def downgrade() -> None:
    op.drop_table("ticker_blacklist", schema="signals")
