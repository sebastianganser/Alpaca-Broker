"""Add options IV and analyst estimates features to feature_snapshots table.

Revision ID: 028
Revises: 027
Create Date: 2026-09-04 11:15:52
"""
import sqlalchemy as sa
from alembic import op

revision = "028"
down_revision = "027"

def upgrade() -> None:
    # Options IV Features
    op.add_column("feature_snapshots", sa.Column("options_iv_atm_30d", sa.Numeric(8, 4), nullable=True), schema="signals")
    op.add_column("feature_snapshots", sa.Column("options_iv_skew_25d", sa.Numeric(8, 4), nullable=True), schema="signals")
    op.add_column("feature_snapshots", sa.Column("options_iv_term_slope", sa.Numeric(8, 4), nullable=True), schema="signals")
    op.add_column("feature_snapshots", sa.Column("options_iv_put_call_oi", sa.Numeric(8, 4), nullable=True), schema="signals")
    
    # Estimates Features
    op.add_column("feature_snapshots", sa.Column("eps_revision_pct_30d", sa.Numeric(10, 6), nullable=True), schema="signals")
    op.add_column("feature_snapshots", sa.Column("eps_revision_pct_90d", sa.Numeric(10, 6), nullable=True), schema="signals")
    op.add_column("feature_snapshots", sa.Column("revenue_revision_pct_30d", sa.Numeric(10, 6), nullable=True), schema="signals")
    op.add_column("feature_snapshots", sa.Column("eps_revisions_net_7d", sa.Integer(), nullable=True), schema="signals")
    op.add_column("feature_snapshots", sa.Column("eps_revisions_net_30d", sa.Integer(), nullable=True), schema="signals")

def downgrade() -> None:
    # Estimates Features
    op.drop_column("feature_snapshots", "eps_revisions_net_30d", schema="signals")
    op.drop_column("feature_snapshots", "eps_revisions_net_7d", schema="signals")
    op.drop_column("feature_snapshots", "revenue_revision_pct_30d", schema="signals")
    op.drop_column("feature_snapshots", "eps_revision_pct_90d", schema="signals")
    op.drop_column("feature_snapshots", "eps_revision_pct_30d", schema="signals")
    
    # Options IV Features
    op.drop_column("feature_snapshots", "options_iv_put_call_oi", schema="signals")
    op.drop_column("feature_snapshots", "options_iv_term_slope", schema="signals")
    op.drop_column("feature_snapshots", "options_iv_skew_25d", schema="signals")
    op.drop_column("feature_snapshots", "options_iv_atm_30d", schema="signals")
