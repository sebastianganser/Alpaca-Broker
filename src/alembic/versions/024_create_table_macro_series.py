"""Create macro_series table.

Sprint 9.5b D1: FRED Macro Regime Collector.
Stores daily observations of macroeconomic indicators
(VIX, Treasury yields, HY spread, Dollar index, Inflation expectations).

Revision ID: 024
Revises: 023
"""

from alembic import op
import sqlalchemy as sa

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create new macro_series table
    op.create_table(
        "macro_series",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("series_id", sa.String(30), nullable=False),
        sa.Column("obs_date", sa.Date, nullable=False),
        sa.Column("value", sa.Numeric(16, 6)),
        sa.Column("source", sa.String(20), nullable=False, server_default="fred"),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("series_id", "obs_date", name="uq_macro_series_dedup"),
        schema="signals",
    )
    op.create_index(
        "idx_macro_series_id", "macro_series",
        ["series_id"], schema="signals",
    )
    op.create_index(
        "idx_macro_obs_date", "macro_series",
        ["obs_date"], schema="signals",
    )

    # Add macro feature columns to feature_snapshots
    new_columns = [
        # D1: Macro features
        ("macro_yield_spread", sa.Numeric(10, 4)),
        ("macro_vix", sa.Numeric(10, 2)),
        ("macro_vix_regime", sa.Integer),
        ("macro_hy_spread", sa.Numeric(10, 4)),
        ("macro_dollar_index", sa.Numeric(10, 2)),
        ("macro_inflation_expectation", sa.Numeric(10, 4)),
        # B2: Earnings SUE
        ("sue_last", sa.Numeric(10, 4)),
        ("days_since_last_earnings", sa.Integer),
        # E3: Sentiment extension
        ("news_volume_ratio_7d", sa.Numeric(10, 4)),
        # E4: Liquidity
        ("dollar_volume_20d", sa.Numeric(20, 0)),
        ("amihud_illiquidity_20d", sa.Numeric(16, 6)),
        # E2: Insider buy ratio
        ("insider_buy_ratio_30d", sa.Numeric(6, 4)),
        ("insider_buy_ratio_90d", sa.Numeric(6, 4)),
        # E1: 13F deltas
        ("form13f_exited_positions_count", sa.Integer),
        ("form13f_holder_delta_qoq", sa.Numeric(10, 4)),
        # D2: Market breadth
        ("breadth_advance_decline", sa.Numeric(6, 4)),
        ("breadth_pct_above_sma50", sa.Numeric(6, 4)),
    ]
    for col_name, col_type in new_columns:
        op.add_column(
            "feature_snapshots",
            sa.Column(col_name, col_type),
            schema="signals",
        )


def downgrade() -> None:
    # Drop new columns from feature_snapshots
    for col_name in [
        "breadth_pct_above_sma50", "breadth_advance_decline",
        "form13f_holder_delta_qoq", "form13f_exited_positions_count",
        "insider_buy_ratio_90d", "insider_buy_ratio_30d",
        "amihud_illiquidity_20d", "dollar_volume_20d",
        "news_volume_ratio_7d",
        "days_since_last_earnings", "sue_last",
        "macro_inflation_expectation", "macro_dollar_index", "macro_hy_spread",
        "macro_vix_regime", "macro_vix", "macro_yield_spread",
    ]:
        op.drop_column("feature_snapshots", col_name, schema="signals")

    # Drop macro_series table
    op.drop_index("idx_macro_obs_date", table_name="macro_series", schema="signals")
    op.drop_index("idx_macro_series_id", table_name="macro_series", schema="signals")
    op.drop_table("macro_series", schema="signals")
