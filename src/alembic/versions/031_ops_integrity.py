"""Ops integrity: timestamptz for logs/news, cascade FK, collection_log indexes.

Revision ID: 031
Revises: 030
Create Date: 2026-10-05

* ``collection_log.started_at/finished_at`` → ``timestamptz``. Historic values
  were written with naive ``datetime.now()`` inside the container
  (TZ=Europe/Berlin) and are therefore interpreted as Berlin wall clock.
* ``news_articles.published_at/fetched_at``, ``news_sentiment.scored_at`` and
  ``ticker_blacklist.detected_at`` → ``timestamptz``. These were either
  written by the server default ``now()`` or as *aware* UTC datetimes
  (Alpaca news, psycopg2 sends ``…+00:00::timestamptz``); PostgreSQL
  converted both into the session ``TimeZone`` when storing them into a
  ``timestamp without time zone`` column. The app never set a session
  timezone, so ``current_setting('TimeZone')`` of this (default) migration
  session is the zone the stored wall-clock values are in.
* ``fk_news_sentiment_article_id`` gets ``ON DELETE CASCADE`` so the weekly
  data-retention job can delete old articles.
* Indexes on ``collection_log (collector_name, started_at DESC)`` and
  ``(started_at)`` for dashboard / health / retention look-ups.
"""

from alembic import op

revision = "031"
down_revision = "030"

SCHEMA = "signals"

# (table, column, source timezone expression)
_TZ_COLUMNS = [
    ("collection_log", "started_at", "'Europe/Berlin'"),
    ("collection_log", "finished_at", "'Europe/Berlin'"),
    ("news_articles", "published_at", "current_setting('TimeZone')"),
    ("news_articles", "fetched_at", "current_setting('TimeZone')"),
    ("news_sentiment", "scored_at", "current_setting('TimeZone')"),
    ("ticker_blacklist", "detected_at", "current_setting('TimeZone')"),
]


def upgrade() -> None:
    for table, column, tz in _TZ_COLUMNS:
        op.execute(
            f"ALTER TABLE {SCHEMA}.{table} "
            f"ALTER COLUMN {column} TYPE timestamptz "
            f"USING {column} AT TIME ZONE {tz}"
        )

    # news_sentiment → news_articles: ON DELETE CASCADE
    op.drop_constraint(
        "fk_news_sentiment_article_id", "news_sentiment",
        schema=SCHEMA, type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_news_sentiment_article_id",
        "news_sentiment", "news_articles",
        ["article_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA,
        ondelete="CASCADE",
    )

    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_collection_log_collector_started "
        f"ON {SCHEMA}.collection_log (collector_name, started_at DESC)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_collection_log_started_at "
        f"ON {SCHEMA}.collection_log (started_at)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_collection_log_started_at")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_collection_log_collector_started")

    op.drop_constraint(
        "fk_news_sentiment_article_id", "news_sentiment",
        schema=SCHEMA, type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_news_sentiment_article_id",
        "news_sentiment", "news_articles",
        ["article_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA,
    )

    for table, column, tz in reversed(_TZ_COLUMNS):
        op.execute(
            f"ALTER TABLE {SCHEMA}.{table} "
            f"ALTER COLUMN {column} TYPE timestamp "
            f"USING {column} AT TIME ZONE {tz}"
        )
