"""Forecast pipeline tables; the empty stage-2 tables are dropped.

Revision ID: 039
Revises: 038
Create Date: 2026-10-18

Plan ``docs/2026-10-17_Plan_Strategieneutral.md`` step 4 / ``docs/FORECASTS.md``:

* ``forecast_reviews`` - one row per ingested ``forecasts.yaml`` (idempotent by
  ``file_sha256``; a changed file is a new review, forecasts are append-only);
* ``forecasts`` - one row per ticker x horizon of a file;
* ``pack_candidates`` - the screen candidates of a context pack (written by
  the pack generator, benchmark of the value-add comparison);
* ``forecast_criterion_samples`` - the frozen decisive sample of the
  pre-registered criterion and its verdict, each written once.

The stage-2 tables of migration 035 (barrier-trade ``decisions.yaml``) never
held a row and are replaced by the tables above (Sebastian approved dropping
them, 2026-10-17). 035 itself is not edited; this migration drops them with
``DROP TABLE IF EXISTS``. The downgrade drops the new tables and recreates the
stage-2 tables exactly as 035 defined them.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op

revision = "039"
down_revision = "038"

SCHEMA = "signals"


def _create_stage2_tables() -> None:
    """The tables of migration 035, verbatim (used by the downgrade)."""
    op.create_table(
        "stage2_reviews",
        sa.Column("session_date", sa.Date(), primary_key=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_mtime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("on_time", sa.Boolean(), nullable=False),
        sa.Column("n_buy", sa.SmallInteger(), nullable=False),
        sa.Column("n_no_entry", sa.SmallInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("source_file", sa.String(300), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "stage2_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_date",
            sa.Date(),
            sa.ForeignKey(f"{SCHEMA}.stage2_reviews.session_date", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("pack_rank", sa.SmallInteger()),
        sa.Column("reason", sa.Text()),
        sa.Column("limit_price", sa.Numeric(12, 4)),
        sa.Column("target_pct", sa.Numeric(8, 4)),
        sa.Column("stop_pct", sa.Numeric(8, 4)),
        sa.CheckConstraint(
            "action IN ('buy', 'no_entry')", name="ck_stage2_decisions_action"
        ),
        sa.UniqueConstraint("session_date", "ticker", name="uq_stage2_session_ticker"),
        schema=SCHEMA,
    )


def upgrade() -> None:
    # CHECK constraint names are the short ORM names: the naming convention
    # (``ck_%(table_name)s_%(constraint_name)s``) of the Alembic metadata expands
    # them to ``ck_<table>_<name>``, exactly as for the ORM.
    op.create_table(
        "forecast_reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("file_path", sa.String(300), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("file_mtime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema", sa.String(40)),
        sa.Column("forecaster_version", sa.String(64)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("on_time", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("n_forecasts", sa.SmallInteger(), nullable=False),
        sa.Column("n_rejected", sa.SmallInteger(), nullable=False),
        sa.Column("errors", JSONB()),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("status IN ('ok', 'partial', 'rejected')", name="status"),
        sa.UniqueConstraint("file_sha256", name="uq_forecast_reviews_file_sha256"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_forecast_reviews_session_date",
        "forecast_reviews",
        ["session_date"],
        schema=SCHEMA,
    )

    op.create_table(
        "forecasts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "review_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{SCHEMA}.forecast_reviews.id"),
            nullable=False,
        ),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("horizon_days", sa.SmallInteger(), nullable=False),
        sa.Column("direction", sa.String(7), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("conviction", sa.SmallInteger()),
        sa.Column("expected_pct", sa.Numeric(8, 3)),
        sa.Column("drivers", ARRAY(sa.String(20))),
        sa.Column("reason", sa.Text()),
        sa.Column("risks", sa.Text()),
        sa.Column("forecaster_version", sa.String(64), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("on_time", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("horizon_days IN (5, 20, 60)", name="horizon_days"),
        sa.CheckConstraint("direction IN ('up', 'down', 'neutral')", name="direction"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.5 AND confidence <= 0.95)",
            name="confidence",
        ),
        sa.CheckConstraint(
            "conviction IS NULL OR (conviction >= 1 AND conviction <= 5)",
            name="conviction",
        ),
        sa.CheckConstraint(
            "direction = 'neutral' OR "
            "(confidence IS NOT NULL AND conviction IS NOT NULL)",
            name="directional_scored",
        ),
        sa.UniqueConstraint(
            "review_id",
            "ticker",
            "horizon_days",
            name="uq_forecasts_review_ticker_horizon",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_forecasts_session_date_horizon_days",
        "forecasts",
        ["session_date", "horizon_days"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_forecasts_forecaster_version_horizon_days",
        "forecasts",
        ["forecaster_version", "horizon_days"],
        schema=SCHEMA,
    )

    op.create_table(
        "pack_candidates",
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("bucket", sa.String(10), nullable=False),
        sa.Column("bucket_rank", sa.SmallInteger()),
        sa.Column("screen_version", sa.String(20), nullable=False),
        sa.Column("gate_ok", sa.Boolean(), nullable=False),
        sa.Column("composite", sa.Numeric(9, 6)),
        sa.Column("evidence_breadth", sa.SmallInteger()),
        sa.Column("group_scores", JSONB()),
        sa.Column("flags", JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "session_date", "ticker", "bucket", name="pk_pack_candidates"
        ),
        sa.CheckConstraint(
            "bucket IN ('bull', 'bear', 'random', 'requested')",
            name="bucket",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "forecast_criterion_samples",
        sa.Column("sample_key", sa.String(64), primary_key=True),
        sa.Column("horizon", sa.SmallInteger(), nullable=False),
        sa.Column("forecaster_version", sa.String(64), nullable=False),
        sa.Column("criterion_mode", sa.String(20), nullable=False),
        sa.Column("min_sessions", sa.SmallInteger(), nullable=False),
        sa.Column("min_per_side", sa.SmallInteger(), nullable=False),
        sa.Column("n_up", sa.Integer(), nullable=False),
        sa.Column("n_down", sa.Integer(), nullable=False),
        sa.Column("sessions", JSONB(), nullable=False),
        sa.Column("forecast_ids", JSONB(), nullable=False),
        sa.Column(
            "frozen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("verdict_values", JSONB()),
        sa.Column("verdict", JSONB()),
        sa.Column("verdict_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "criterion_mode IN ('system', 'claude_value_add')",
            name="criterion_mode",
        ),
        sa.CheckConstraint(
            "(verdict IS NULL) = (verdict_values IS NULL) "
            "AND (verdict IS NULL) = (verdict_at IS NULL)",
            name="verdict_complete",
        ),
        sa.UniqueConstraint(
            "horizon",
            "forecaster_version",
            name="uq_forecast_criterion_samples_horizon_version",
        ),
        schema=SCHEMA,
    )

    # the stage-2 tables never held a row; replaced by the tables above
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.stage2_decisions")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.stage2_reviews")


def downgrade() -> None:
    op.drop_table("forecast_criterion_samples", schema=SCHEMA)
    op.drop_table("pack_candidates", schema=SCHEMA)
    op.drop_table("forecasts", schema=SCHEMA)
    op.drop_table("forecast_reviews", schema=SCHEMA)
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("stage2_reviews", schema=SCHEMA):
        _create_stage2_tables()
