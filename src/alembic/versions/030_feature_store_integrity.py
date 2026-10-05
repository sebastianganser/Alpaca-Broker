"""Feature store integrity (review 2026-10, derived/ML layer).

Revision ID: 030
Revises: 029
Create Date: 2026-10-05

Contents
--------
* feature_snapshots.feature_version VARCHAR(20) NULL (H1). NULL marks rows
  computed before FEATURE_VERSION 2026.10-1 (the repair script rewrites
  them all).
* feature_snapshots on non-NYSE sessions are deleted (H3):
    - weekends (ISODOW 6/7)
    - weekdays without a real SPY price row inside SPY's price history
      (exchange holidays). Rows before/after SPY's history are kept.
* forward-return targets are reset to NULL (C4: new definition
  close(d+h)/open(d+1) − 1 on the NYSE calendar). The nightly
  ``TargetBackfillComputer.backfill_all()`` (or the repair script)
  recomputes them; old and new definitions are never mixed.
* technical_indicators rows on gap-filled (``is_extrapolated``) price
  rows are deleted (H7).
* ark_deltas: new/closed positions get signed full-size deltas (M6):
  new = +curr, closed = −prev (where NULL).

Data fixes are not reverted on downgrade (only the column is dropped).
"""

import sqlalchemy as sa

from alembic import op

revision = "030"
down_revision = "029"

SCHEMA = "signals"


def upgrade() -> None:
    op.add_column(
        "feature_snapshots",
        sa.Column("feature_version", sa.String(20), nullable=True),
        schema=SCHEMA,
    )

    # H3: weekends
    op.execute(
        "DELETE FROM signals.feature_snapshots "
        "WHERE EXTRACT(ISODOW FROM snapshot_date) IN (6, 7)"
    )
    # H3: exchange holidays = weekdays without a real SPY bar inside SPY's
    # history (bounded so that rows outside the SPY range are untouched)
    op.execute("""
        WITH spy AS (
            SELECT trade_date FROM signals.prices_daily
            WHERE ticker = 'SPY' AND is_extrapolated IS NOT TRUE
        ), bounds AS (
            SELECT min(trade_date) AS lo, max(trade_date) AS hi FROM spy
        )
        DELETE FROM signals.feature_snapshots fs
        USING bounds b
        WHERE b.lo IS NOT NULL
          AND fs.snapshot_date BETWEEN b.lo AND b.hi
          AND NOT EXISTS (
              SELECT 1 FROM spy s WHERE s.trade_date = fs.snapshot_date
          )""")

    # C4: targets are recomputed with the new definition
    op.execute(
        "UPDATE signals.feature_snapshots "
        "SET return_1d = NULL, return_5d = NULL, "
        "    return_20d = NULL, return_60d = NULL "
        "WHERE return_1d IS NOT NULL OR return_5d IS NOT NULL "
        "   OR return_20d IS NOT NULL OR return_60d IS NOT NULL"
    )

    # H7: indicators computed on gap-filled price rows
    op.execute("""
        DELETE FROM signals.technical_indicators ti
        USING signals.prices_daily p
        WHERE p.ticker = ti.ticker
          AND p.trade_date = ti.trade_date
          AND p.is_extrapolated IS TRUE""")

    # M6: signed deltas for new / closed ARK positions
    op.execute(
        "UPDATE signals.ark_deltas "
        "SET shares_delta = COALESCE(shares_delta, shares_curr), "
        "    weight_delta = COALESCE(weight_delta, weight_curr) "
        "WHERE delta_type = 'new_position'"
    )
    op.execute(
        "UPDATE signals.ark_deltas "
        "SET shares_delta = COALESCE(shares_delta, -shares_prev), "
        "    weight_delta = COALESCE(weight_delta, -weight_prev) "
        "WHERE delta_type = 'closed'"
    )


def downgrade() -> None:
    # Data fixes (deleted rows, reset targets, ARK deltas) are not reverted.
    op.drop_column("feature_snapshots", "feature_version", schema=SCHEMA)
