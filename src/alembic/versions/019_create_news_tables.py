"""019: Create news_articles and news_sentiment tables (Sprint 8c).

Raw layer: news_articles stores financial news from Alpaca News API.
Derived layer: news_sentiment stores FinBERT (later Haiku) sentiment scores.

The symbols column uses PostgreSQL ARRAY with GIN index for efficient
"contains ticker X" queries. news_sentiment has a per-ticker granularity –
a multi-ticker article generates one sentiment row per mentioned ticker.

Revision ID: 019
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Raw Layer: News Articles ──
    op.create_table(
        "news_articles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("article_id", sa.String(100), nullable=True),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("author", sa.String(200), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("article_url", sa.Text(), nullable=True),
        sa.Column("symbols", ARRAY(sa.String(20)), nullable=True),
        sa.Column("is_global", sa.Boolean(), server_default="false"),
        sa.Column(
            "fetched_at",
            sa.DateTime(),
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("article_id", name="uq_news_articles_article_id"),
        schema="signals",
    )

    op.create_index(
        "idx_news_published",
        "news_articles",
        ["published_at"],
        schema="signals",
    )
    op.create_index(
        "idx_news_symbols",
        "news_articles",
        ["symbols"],
        schema="signals",
        postgresql_using="gin",
    )

    # ── Derived Layer: News Sentiment ──
    op.create_table(
        "news_sentiment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=True),
        sa.Column("sentiment_label", sa.String(20), nullable=False),
        sa.Column("sentiment_score", sa.Numeric(6, 4), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=True),
        sa.Column("model_version", sa.String(50), nullable=False),
        sa.Column(
            "scored_at",
            sa.DateTime(),
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["signals.news_articles.id"],
            name="fk_news_sentiment_article_id",
        ),
        sa.UniqueConstraint(
            "article_id", "ticker", "model_version",
            name="uq_news_sentiment_article_ticker_model",
        ),
        schema="signals",
    )

    op.create_index(
        "idx_sentiment_ticker_date",
        "news_sentiment",
        ["ticker", "scored_at"],
        schema="signals",
    )
    op.create_index(
        "idx_sentiment_model",
        "news_sentiment",
        ["model_version"],
        schema="signals",
    )


def downgrade() -> None:
    op.drop_index("idx_sentiment_model", "news_sentiment", schema="signals")
    op.drop_index("idx_sentiment_ticker_date", "news_sentiment", schema="signals")
    op.drop_table("news_sentiment", schema="signals")
    op.drop_index("idx_news_symbols", "news_articles", schema="signals")
    op.drop_index("idx_news_published", "news_articles", schema="signals")
    op.drop_table("news_articles", schema="signals")
