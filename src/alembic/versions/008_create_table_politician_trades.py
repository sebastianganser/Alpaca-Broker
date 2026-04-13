"""Create politician_trades table.

Revision ID: 008
Revises: 007
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # politician_trades – US Congress stock trades (raw layer)
    op.create_table(
        "politician_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("politician_name", sa.String(200)),
        sa.Column("chamber", sa.String(20)),  # Senate, House
        sa.Column("party", sa.String(20)),
        sa.Column("state", sa.String(2)),
        sa.Column("ticker", sa.String(20)),
        sa.Column("transaction_date", sa.Date()),
        sa.Column("disclosure_date", sa.Date()),
        sa.Column("transaction_type", sa.String(20)),  # Purchase, Sale
        sa.Column("amount_range", sa.String(50)),
        sa.Column("owner", sa.String(50)),  # Self, Spouse, Joint, Child
        sa.Column("asset_description", sa.Text()),
        sa.Column("comment", sa.Text()),
        sa.Column("source_url", sa.Text()),
        sa.Column("raw_data", JSONB()),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        schema="signals",
    )
    op.create_index(
        "idx_politician_ticker",
        "politician_trades",
        ["ticker"],
        schema="signals",
    )
    op.create_index(
        "idx_politician_date",
        "politician_trades",
        ["transaction_date"],
        schema="signals",
    )
    # Dedup constraint
    op.create_unique_constraint(
        "uq_politician_trade_dedup",
        "politician_trades",
        ["politician_name", "ticker", "transaction_date",
         "transaction_type", "amount_range"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_politician_trade_dedup",
        "politician_trades",
        schema="signals",
    )
    op.drop_index(
        "idx_politician_date",
        table_name="politician_trades",
        schema="signals",
    )
    op.drop_index(
        "idx_politician_ticker",
        table_name="politician_trades",
        schema="signals",
    )
    op.drop_table("politician_trades", schema="signals")
