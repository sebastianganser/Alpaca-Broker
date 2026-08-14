"""Create analysis_reports table for monthly feature analysis.

Revision ID: 021
Revises: 020
Create Date: 2026-08-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("snapshot_count", sa.Integer(), nullable=False),
        sa.Column("ticker_count", sa.Integer(), nullable=False),
        sa.Column("date_range_start", sa.Date(), nullable=False),
        sa.Column("date_range_end", sa.Date(), nullable=False),
        sa.Column("feature_correlations", JSONB(), nullable=True),
        sa.Column("feature_importance_rf", JSONB(), nullable=True),
        sa.Column("feature_importance_lasso", JSONB(), nullable=True),
        sa.Column("hypothesis_results", JSONB(), nullable=True),
        sa.Column("consensus_features", JSONB(), nullable=True),
        sa.Column("html_report", sa.Text(), nullable=True),
        sa.Column("computation_time_seconds", sa.Float(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_date"),
        schema="signals",
    )
    op.create_index(
        "idx_analysis_reports_date",
        "analysis_reports",
        ["report_date"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_analysis_reports_date",
        table_name="analysis_reports",
        schema="signals",
    )
    op.drop_table("analysis_reports", schema="signals")
