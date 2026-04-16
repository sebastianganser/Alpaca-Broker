"""017: Add unique constraint on insider_clusters (ticker, cluster_start).

Prevents duplicate cluster entries when the cluster computer runs
multiple times. First removes existing duplicates, keeping the row
with the highest ID (most recent computation).

Revision ID: 017
"""

from alembic import op
import sqlalchemy as sa

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Remove duplicate clusters, keeping the highest ID per (ticker, cluster_start)
    op.execute("""
        DELETE FROM signals.insider_clusters
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM signals.insider_clusters
            GROUP BY ticker, cluster_start
        )
    """)

    # Step 2: Add unique constraint
    op.create_unique_constraint(
        "uq_insider_cluster_ticker_start",
        "insider_clusters",
        ["ticker", "cluster_start"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_insider_cluster_ticker_start",
        "insider_clusters",
        schema="signals",
    )
