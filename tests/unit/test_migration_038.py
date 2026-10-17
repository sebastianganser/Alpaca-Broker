"""Migration 038 removes the trailing-stop label columns again (no DB).

Decision 2026-10-17 "Scope: Alpaca-Broker forecasts, a separate bot trades".
Migration 037 stays untouched (applied in production); 038 drops its columns.
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

from trading_signals.db.models.features import (
    FEATURE_COLUMNS,
    LABEL_COLUMNS,
    TARGET_COLUMNS,
    FeatureSnapshot,
)

VERSIONS = Path(__file__).resolve().parents[2] / "src" / "alembic" / "versions"
TRAIL_COLUMNS = ("return_trail_60d", "trail_exit_day", "trail_stopped")


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
    mig = _load("038_drop_trailing_label.py")
    assert mig.revision == "038" and mig.down_revision == "037"
    assert _load("037_trailing_label.py").revision == "037"  # not edited


def test_upgrade_drops_the_three_columns_idempotently():
    executed = _run(_load("038_drop_trailing_label.py"), "upgrade")
    assert executed == [
        f"ALTER TABLE signals.feature_snapshots DROP COLUMN IF EXISTS {col}"
        for col in TRAIL_COLUMNS
    ]


def test_downgrade_restores_exactly_the_037_columns():
    mig037 = _load("037_trailing_label.py")
    mig038 = _load("038_drop_trailing_label.py")
    assert mig038.TABLE == mig037.TABLE
    assert mig038.COLUMNS == mig037.COLUMNS  # same names and SQL types as 037
    assert [c for c, _ in mig038.COLUMNS] == list(TRAIL_COLUMNS)
    executed = _run(mig038, "downgrade")
    assert executed == [
        f"ALTER TABLE signals.feature_snapshots ADD COLUMN IF NOT EXISTS {col} {typ}"
        for col, typ in mig037.COLUMNS
    ]


def test_orm_no_longer_has_the_trailing_columns():
    table_cols = FeatureSnapshot.__table__.columns
    for col in TRAIL_COLUMNS:
        assert col not in table_cols
        assert col not in TARGET_COLUMNS
        assert col not in LABEL_COLUMNS
        assert col not in FEATURE_COLUMNS
