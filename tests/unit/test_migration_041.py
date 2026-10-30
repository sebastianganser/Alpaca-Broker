"""Migration 041 removes the barrier trade label columns again (no DB).

Plan 2026-10-17 step 6, decision "Strategy-Neutral Rework Approved". Migration
033 stays untouched (applied in production); 041 drops its columns. The real
fresh-DB run (upgrade, ``alembic check``, downgrade, upgrade) is documented in
docs/DATABASE.md and CLAUDE.md.
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from trading_signals.db.models.features import (
    FEATURE_COLUMNS,
    TARGET_COLUMNS,
    FeatureSnapshot,
)

VERSIONS = Path(__file__).resolve().parents[2] / "src" / "alembic" / "versions"
BARRIER_COLUMNS = ("return_barrier_14d", "barrier_outcome", "barrier_ambiguous")


def _load(filename: str):
    spec = importlib.util.spec_from_file_location(
        filename.split("_")[0], VERSIONS / filename
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, fn: str) -> list[str]:
    executed: list[str] = []
    op = MagicMock()
    op.execute.side_effect = lambda sql: executed.append(str(sql))
    with patch.object(mod, "op", op):
        getattr(mod, fn)()
    return executed


def test_revision_chain():
    mig = _load("041_drop_barrier_label.py")
    assert mig.revision == "041" and mig.down_revision == "040"
    assert _load("040_sec_filings_financials.py").revision == "040"  # not edited
    assert _load("033_barrier_label.py").revision == "033"  # not edited


def test_upgrade_drops_the_three_columns_idempotently():
    executed = _run(_load("041_drop_barrier_label.py"), "upgrade")
    assert executed == [
        f"ALTER TABLE signals.feature_snapshots DROP COLUMN IF EXISTS {col}"
        for col in BARRIER_COLUMNS
    ]


def test_downgrade_restores_exactly_the_033_columns():
    mig033 = _load("033_barrier_label.py")
    op033 = MagicMock()
    with patch.object(mig033, "op", op033):
        mig033.upgrade()
    original = {
        call.args[1].name: call.args[1].type.compile(dialect=postgresql.dialect())
        for call in op033.add_column.call_args_list
    }
    mig041 = _load("041_drop_barrier_label.py")
    assert tuple(original) == BARRIER_COLUMNS
    assert dict(mig041.COLUMNS) == original  # same names and SQL types as 033
    assert [c for c, _ in mig041.COLUMNS] == list(BARRIER_COLUMNS)
    assert _run(mig041, "downgrade") == [
        f"ALTER TABLE signals.feature_snapshots ADD COLUMN IF NOT EXISTS {col} {typ}"
        for col, typ in mig041.COLUMNS
    ]


def test_orm_no_longer_has_the_barrier_columns():
    table_cols = FeatureSnapshot.__table__.columns
    for col in BARRIER_COLUMNS:
        assert col not in table_cols
        assert col not in TARGET_COLUMNS
        assert col not in FEATURE_COLUMNS
        assert not hasattr(FeatureSnapshot, col)
