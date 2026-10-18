"""Migration 039: forecast tables, stage-2 tables dropped (no DB).

``alembic check`` does not compare CHECK constraints, so this test builds the
tables from the migration's ``op.create_table`` calls and compares the DDL with
the ORM models. The real fresh-DB run (upgrade, ``alembic check``, downgrade,
upgrade) is documented in docs/DATABASE.md and CLAUDE.md.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import trading_signals.db.models  # noqa: F401  (registers every model)
from trading_signals.db.base import Base, convention

VERSIONS = Path(__file__).resolve().parents[2] / "src" / "alembic" / "versions"
NEW_TABLES = (
    "forecast_reviews",
    "forecasts",
    "pack_candidates",
    "forecast_criterion_samples",
    "forecast_file_seals",
)


def _load(filename: str):
    spec = importlib.util.spec_from_file_location(
        filename.split("_")[0], VERSIONS / filename
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, fn: str, has_stage2: bool = False) -> MagicMock:
    op = MagicMock()
    inspector = MagicMock()
    inspector.has_table.return_value = has_stage2
    with (
        patch.object(mod, "op", op),
        patch.object(mod.sa, "inspect", return_value=inspector),
    ):
        getattr(mod, fn)()
    return op


def _ddl(table: sa.Table) -> str:
    text = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    return re.sub(r"\s+", " ", text).strip()


def _migration_tables(op: MagicMock) -> dict[str, sa.Table]:
    meta = sa.MetaData(naming_convention=convention)
    tables = {}
    for call in op.create_table.call_args_list:
        name, *items = call.args
        tables[name] = sa.Table(name, meta, *items, **call.kwargs)
    return tables


def test_revision_chain_and_untouched_predecessors():
    mig = _load("039_forecasts.py")
    assert mig.revision == "039" and mig.down_revision == "038"
    assert _load("038_drop_trailing_label.py").revision == "038"
    assert _load("035_stage2_decisions.py").revision == "035"  # not edited


def test_upgrade_creates_the_new_tables_and_drops_the_stage2_tables():
    op = _run(_load("039_forecasts.py"), "upgrade")
    created = [c.args[0] for c in op.create_table.call_args_list]
    assert created == [
        "forecast_reviews",
        "forecasts",
        "pack_candidates",
        "forecast_criterion_samples",
        "forecast_file_seals",
    ]
    assert all(c.kwargs["schema"] == "signals" for c in op.create_table.call_args_list)
    executed = [str(c.args[0]) for c in op.execute.call_args_list]
    assert executed == [
        "DROP TABLE IF EXISTS signals.stage2_decisions",  # child first
        "DROP TABLE IF EXISTS signals.stage2_reviews",
    ]
    indexes = {c.args[0] for c in op.create_index.call_args_list}
    assert indexes == {
        "ix_forecast_reviews_session_date",
        "ix_forecasts_session_date_horizon_days",
        "ix_forecasts_forecaster_version_horizon_days",
    }


def test_downgrade_drops_the_new_tables_and_restores_the_stage2_tables_like_035():
    mod = _load("039_forecasts.py")
    op = _run(mod, "downgrade")
    dropped = [c.args[0] for c in op.drop_table.call_args_list]
    assert dropped == [
        "forecast_file_seals",
        "forecast_criterion_samples",
        "pack_candidates",
        "forecasts",
        "forecast_reviews",
    ]  # forecasts (FK) before forecast_reviews
    restored = _migration_tables(op)
    original = _run(_load("035_stage2_decisions.py"), "upgrade")
    originals = _migration_tables(original)
    assert set(restored) == set(originals) == {"stage2_reviews", "stage2_decisions"}
    for name in originals:
        assert _ddl(restored[name]) == _ddl(originals[name])
    # idempotent: nothing is recreated when the tables are still there
    assert not _run(mod, "downgrade", has_stage2=True).create_table.called


def test_the_orm_mirrors_the_migrated_schema_including_check_constraints():
    op = _run(_load("039_forecasts.py"), "upgrade")
    migrated = _migration_tables(op)
    assert set(migrated) == set(NEW_TABLES)
    for name in NEW_TABLES:
        orm = Base.metadata.tables[f"signals.{name}"]
        assert _ddl(orm) == _ddl(migrated[name]), name


def test_the_stage2_tables_are_gone_from_the_orm():
    assert not [t for t in Base.metadata.tables if "stage2" in t]
    assert {f"signals.{n}" for n in NEW_TABLES} <= set(Base.metadata.tables)


def test_the_seal_table_has_the_contracted_columns_and_unique_key():
    op = _run(_load("039_forecasts.py"), "upgrade")
    seals = _migration_tables(op)["forecast_file_seals"]
    assert {c.name for c in seals.columns} == {
        "id",
        "session_date",
        "file_path",
        "file_sha256",
        "file_size",
        "sealed_at",
    }
    assert all(not c.nullable for c in seals.columns if c.name != "id")
    assert seals.c.sealed_at.type.timezone is True  # TIMESTAMPTZ
    assert seals.c.sealed_at.server_default is not None
    unique = [c for c in seals.constraints if isinstance(c, sa.UniqueConstraint)]
    assert [[col.name for col in u.columns] for u in unique] == [
        ["session_date", "file_sha256"]
    ]
