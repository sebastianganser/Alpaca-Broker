"""Create insider_trades and insider_clusters tables.

Revision ID: 006
Revises: 005
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # insider_trades – SEC Form 4 individual transactions (raw layer)
    op.create_table(
        "insider_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ticker", sa.String(20)),
        sa.Column("company_name", sa.String(200)),
        sa.Column("cik", sa.String(20)),
        sa.Column("insider_name", sa.String(200)),
        sa.Column("insider_title", sa.String(200)),
        sa.Column("transaction_date", sa.Date()),
        sa.Column("filing_date", sa.Date()),
        sa.Column("transaction_type", sa.String(20)),
        sa.Column("shares", sa.Numeric(20, 4)),
        sa.Column("price_per_share", sa.Numeric(16, 4)),
        sa.Column("total_value", sa.Numeric(20, 2)),
        sa.Column("shares_owned_after", sa.Numeric(20, 4)),
        sa.Column("is_derivative", sa.Boolean(), server_default="false"),
        sa.Column("form4_url", sa.Text()),
        sa.Column("raw_data", JSONB()),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        schema="signals",
    )
    op.create_index(
        "idx_insider_ticker_date",
        "insider_trades",
        ["ticker", "transaction_date"],
        schema="signals",
    )
    op.create_index(
        "idx_insider_filing_date",
        "insider_trades",
        ["filing_date"],
        schema="signals",
    )
    # Dedup constraint: same insider, same day, same transaction
    op.create_unique_constraint(
        "uq_insider_trade_dedup",
        "insider_trades",
        ["cik", "insider_name", "transaction_date",
         "transaction_type", "shares", "price_per_share"],
        schema="signals",
    )

    # insider_clusters – derived layer for cluster detection
    op.create_table(
        "insider_clusters",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ticker", sa.String(20)),
        sa.Column("cluster_start", sa.Date()),
        sa.Column("cluster_end", sa.Date()),
        sa.Column("n_insiders", sa.Integer()),
        sa.Column("n_buys", sa.Integer()),
        sa.Column("n_sells", sa.Integer()),
        sa.Column("total_buy_value", sa.Numeric(20, 2)),
        sa.Column("total_sell_value", sa.Numeric(20, 2)),
        sa.Column("cluster_score", sa.Numeric(10, 4)),
        sa.Column("computed_at", sa.DateTime(), server_default=sa.func.now()),
        schema="signals",
    )
    op.create_index(
        "idx_insider_clusters_ticker",
        "insider_clusters",
        ["ticker"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_insider_clusters_ticker",
        table_name="insider_clusters",
        schema="signals",
    )
    op.drop_table("insider_clusters", schema="signals")
    op.drop_constraint(
        "uq_insider_trade_dedup",
        "insider_trades",
        schema="signals",
    )
    op.drop_index(
        "idx_insider_filing_date",
        table_name="insider_trades",
        schema="signals",
    )
    op.drop_index(
        "idx_insider_ticker_date",
        table_name="insider_trades",
        schema="signals",
    )
    op.drop_table("insider_trades", schema="signals")
