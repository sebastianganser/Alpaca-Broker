"""018: Create feature_snapshots table (Sprint 8).

The heart of the project – daily feature vector per ticker aggregated
from all raw and derived signal data. Wide table with ~77 columns
covering point-in-time features, temporal rolling-window features,
and backfillable target return variables.

Revision ID: 018
"""

from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_snapshots",
        # ── Primary Key ──
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),

        # ── ARK Features (point-in-time) ──
        sa.Column("ark_in_etf_count", sa.Integer()),
        sa.Column("ark_total_weight", sa.Numeric(10, 4)),
        sa.Column("ark_weight_delta_1d", sa.Numeric(10, 4)),
        sa.Column("ark_weight_delta_5d", sa.Numeric(10, 4)),
        sa.Column("ark_weight_delta_20d", sa.Numeric(10, 4)),
        sa.Column("ark_conviction_score", sa.Numeric(10, 4)),
        sa.Column("ark_multi_etf_signal", sa.Boolean()),

        # ── ARK Temporal Features ──
        sa.Column("ark_increase_days_10d", sa.Integer()),
        sa.Column("ark_increase_days_20d", sa.Integer()),
        sa.Column("ark_conviction_streak", sa.Integer()),
        sa.Column("ark_weight_trend_20d", sa.Numeric(10, 6)),

        # ── Insider Features (point-in-time) ──
        sa.Column("insider_net_buy_count_30d", sa.Integer()),
        sa.Column("insider_buy_value_30d", sa.Numeric(20, 2)),
        sa.Column("insider_cluster_active", sa.Boolean()),
        sa.Column("insider_cluster_score", sa.Numeric(10, 4)),

        # ── Insider Temporal Features ──
        sa.Column("cluster_count_30d", sa.Integer()),
        sa.Column("cluster_count_60d", sa.Integer()),
        sa.Column("cluster_score_sum_60d", sa.Numeric(10, 4)),
        sa.Column("days_since_last_cluster", sa.Integer()),

        # ── Analyst Features (point-in-time) ──
        sa.Column("analyst_rating_score", sa.Numeric(10, 4)),
        sa.Column("analyst_upgrades_30d", sa.Integer()),
        sa.Column("analyst_price_target_upside", sa.Numeric(10, 4)),

        # ── Analyst Temporal Features ──
        sa.Column("analyst_downgrades_30d", sa.Integer()),
        sa.Column("analyst_net_sentiment_30d", sa.Integer()),
        sa.Column("analyst_net_sentiment_60d", sa.Integer()),
        sa.Column("analyst_upgrade_streak", sa.Integer()),

        # ── Politician Features (dual-date variants) ──
        sa.Column("politician_buy_count_60d_disclosure", sa.Integer()),
        sa.Column("politician_distinct_90d_disclosure", sa.Integer()),
        sa.Column("politician_buy_count_60d_transaction", sa.Integer()),
        sa.Column("politician_distinct_90d_transaction", sa.Integer()),

        # ── 13F Features ──
        sa.Column("form13f_top_holder_count", sa.Integer()),
        sa.Column("form13f_new_positions_count", sa.Integer()),

        # ── Fundamentals (point-in-time) ──
        sa.Column("pe_ratio", sa.Numeric(16, 4)),
        sa.Column("forward_pe", sa.Numeric(16, 4)),
        sa.Column("ps_ratio", sa.Numeric(16, 4)),
        sa.Column("revenue_growth_yoy", sa.Numeric(10, 6)),
        sa.Column("profit_margin", sa.Numeric(10, 6)),
        sa.Column("debt_to_equity", sa.Numeric(16, 4)),

        # ── Fundamentals Temporal Features ──
        sa.Column("pe_trend_4w", sa.Numeric(10, 6)),
        sa.Column("margin_trend_4w", sa.Numeric(10, 6)),

        # ── Technical Indicators ──
        sa.Column("price_vs_sma50", sa.Numeric(10, 4)),
        sa.Column("price_vs_sma200", sa.Numeric(10, 4)),
        sa.Column("rsi_14", sa.Numeric(10, 4)),
        sa.Column("relative_strength_spy", sa.Numeric(10, 4)),
        sa.Column("volume_ratio_20d", sa.Numeric(10, 4)),
        sa.Column("atr_14_pct", sa.Numeric(10, 4)),

        # ── Earnings Features ──
        sa.Column("earnings_days_until", sa.Integer()),
        sa.Column("consecutive_beats", sa.Integer()),
        sa.Column("surprise_trend_3q", sa.Numeric(10, 4)),

        # ── Target Variables (backfilled retrospectively) ──
        sa.Column("return_1d", sa.Numeric(10, 6)),
        sa.Column("return_5d", sa.Numeric(10, 6)),
        sa.Column("return_20d", sa.Numeric(10, 6)),
        sa.Column("return_60d", sa.Numeric(10, 6)),

        # ── Metadata ──
        sa.Column(
            "computed_at",
            sa.DateTime(),
            server_default=sa.func.now(),
        ),

        sa.PrimaryKeyConstraint("snapshot_date", "ticker"),
        schema="signals",
    )

    # Indexes for common query patterns
    op.create_index(
        "idx_features_date",
        "feature_snapshots",
        ["snapshot_date"],
        schema="signals",
    )
    op.create_index(
        "idx_features_ticker",
        "feature_snapshots",
        ["ticker"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_index("idx_features_ticker", "feature_snapshots", schema="signals")
    op.drop_index("idx_features_date", "feature_snapshots", schema="signals")
    op.drop_table("feature_snapshots", schema="signals")
