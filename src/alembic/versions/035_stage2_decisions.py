"""Stage-2 decisions (Claude broker) for the forward test.

Revision ID: 035
Revises: 034
Create Date: 2026-10-07

Concept ``docs/2026-10-06_Konzept_Kurzfrist_Kandidaten.md`` §7.7: the skill
writes ``decisions.yaml`` into the context-pack folder; the nightly step
``stage2_review`` (``derived/stage2_decisions.py``) stores one review row per
pack day and one decision row per reviewed ticker. Trades are evaluated with
the existing barrier label of ``feature_snapshots`` (no result columns here).
"""

import sqlalchemy as sa

from alembic import op

revision = "035"
down_revision = "034"

SCHEMA = "signals"


def upgrade() -> None:
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
            "ingested_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "stage2_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_date", sa.Date(),
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


def downgrade() -> None:
    op.drop_table("stage2_decisions", schema=SCHEMA)
    op.drop_table("stage2_reviews", schema=SCHEMA)
