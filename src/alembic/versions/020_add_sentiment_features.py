"""020: Add sentiment feature columns to feature_snapshots (Sprint 8c).

Adds 6 new nullable columns to the feature_snapshots table for
news-based sentiment features. These are computed by the feature
pipeline from the news_sentiment table.

Revision ID: 020
"""

from alembic import op
import sqlalchemy as sa

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

# All new sentiment columns
_COLUMNS = [
    ("sentiment_avg_7d", sa.Numeric(6, 4)),
    ("sentiment_avg_30d", sa.Numeric(6, 4)),
    ("sentiment_momentum", sa.Numeric(6, 4)),
    ("sentiment_neg_count_7d", sa.Integer()),
    ("sentiment_article_count_7d", sa.Integer()),
    ("market_sentiment_7d", sa.Numeric(6, 4)),
]


def upgrade() -> None:
    for col_name, col_type in _COLUMNS:
        op.add_column(
            "feature_snapshots",
            sa.Column(col_name, col_type, nullable=True),
            schema="signals",
        )


def downgrade() -> None:
    for col_name, _ in reversed(_COLUMNS):
        op.drop_column("feature_snapshots", col_name, schema="signals")
