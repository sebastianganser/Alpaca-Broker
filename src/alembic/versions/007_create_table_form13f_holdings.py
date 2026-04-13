"""Create form13f_holdings table.

Revision ID: 007
Revises: 006
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # form13f_holdings – quarterly institutional holdings (raw layer)
    op.create_table(
        "form13f_holdings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("filer_name", sa.String(200)),
        sa.Column("filer_cik", sa.String(20)),
        sa.Column("report_period", sa.Date()),
        sa.Column("filing_date", sa.Date()),
        sa.Column("ticker", sa.String(20)),
        sa.Column("cusip", sa.String(20)),
        sa.Column("shares", sa.Numeric(20, 4)),
        sa.Column("market_value", sa.Numeric(20, 2)),
        sa.Column("put_call", sa.String(10)),
        sa.Column("source_url", sa.Text()),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        schema="signals",
    )
    op.create_index(
        "idx_13f_ticker",
        "form13f_holdings",
        ["ticker"],
        schema="signals",
    )
    op.create_index(
        "idx_13f_filer_period",
        "form13f_holdings",
        ["filer_cik", "report_period"],
        schema="signals",
    )
    # Dedup constraint: same filer, same period, same CUSIP
    op.create_unique_constraint(
        "uq_13f_holding_dedup",
        "form13f_holdings",
        ["filer_cik", "report_period", "cusip"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_13f_holding_dedup",
        "form13f_holdings",
        schema="signals",
    )
    op.drop_index(
        "idx_13f_filer_period",
        table_name="form13f_holdings",
        schema="signals",
    )
    op.drop_index(
        "idx_13f_ticker",
        table_name="form13f_holdings",
        schema="signals",
    )
    op.drop_table("form13f_holdings", schema="signals")
