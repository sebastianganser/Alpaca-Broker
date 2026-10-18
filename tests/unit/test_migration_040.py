"""Migration 040: SEC filings, filing texts, quarterly financials, more fundamentals.

``alembic check`` compares columns and indexes but not every constraint detail,
so this test builds the tables from the migration's ``op.create_table`` calls
and compares the DDL with the ORM models. The real fresh-DB run (upgrade,
``alembic check``, downgrade, upgrade) is documented in docs/DATABASE.md and
CLAUDE.md.
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
NEW_TABLES = ("sec_filings", "sec_filing_texts", "financials_quarterly")


def _load(filename: str):
    spec = importlib.util.spec_from_file_location(
        filename.split("_")[0], VERSIONS / filename
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(fn: str) -> MagicMock:
    mod = _load("040_sec_filings_financials.py")
    op = MagicMock()
    with patch.object(mod, "op", op):
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


def _type(column: sa.Column) -> str:
    return column.type.compile(dialect=postgresql.dialect())


def test_revision_chain():
    mod = _load("040_sec_filings_financials.py")
    assert mod.revision == "040" and mod.down_revision == "039"
    assert _load("039_forecasts.py").revision == "039"  # not edited


def test_upgrade_creates_the_tables_parent_first_and_adds_the_fundamentals_columns():
    op = _run("upgrade")
    created = [c.args[0] for c in op.create_table.call_args_list]
    assert created == ["sec_filings", "sec_filing_texts", "financials_quarterly"]
    assert all(c.kwargs["schema"] == "signals" for c in op.create_table.call_args_list)
    indexes = {c.args[0] for c in op.create_index.call_args_list}
    assert indexes == {
        "ix_sec_filings_ticker_filing_date",
        "ix_sec_filings_filing_date",
        "ix_sec_filings_cik_filing_date",
        "ix_financials_quarterly_period_end",
    }
    added = [c.args for c in op.add_column.call_args_list]
    assert all(table == "fundamentals_snapshot" for table, _ in added)
    assert [col.name for _, col in added] == [
        "shares_outstanding",
        "float_shares",
        "held_percent_institutions",
        "held_percent_insiders",
        "short_percent_of_float",
        "free_cashflow",
        "total_cash",
        "total_debt",
        "gross_margins",
        "recommendation_mean",
        "recommendation_key",
        "number_of_analyst_opinions",
    ]


def test_downgrade_is_the_exact_reverse():
    op = _run("downgrade")
    assert [c.args[0] for c in op.drop_table.call_args_list] == [
        "financials_quarterly",
        "sec_filing_texts",  # child before its parent
        "sec_filings",
    ]
    dropped = [c.args[1] for c in op.drop_column.call_args_list]
    added = [c.args[1].name for c in _run("upgrade").add_column.call_args_list]
    assert dropped == list(reversed(added))
    assert all(
        c.args[0] == "fundamentals_snapshot" for c in op.drop_column.call_args_list
    )


def test_the_orm_mirrors_the_migrated_tables_including_constraints():
    migrated = _migration_tables(_run("upgrade"))
    assert set(migrated) == set(NEW_TABLES)
    for name in NEW_TABLES:
        orm = Base.metadata.tables[f"signals.{name}"]
        assert _ddl(orm) == _ddl(migrated[name]), name


def test_the_orm_mirrors_the_migrated_indexes():
    op = _run("upgrade")
    migrated = {
        (c.args[0], c.args[1], tuple(c.args[2])) for c in op.create_index.call_args_list
    }
    orm = {
        (index.name, table.name, tuple(col.name for col in index.columns))
        for table in (Base.metadata.tables[f"signals.{n}"] for n in NEW_TABLES)
        for index in table.indexes
    }
    assert migrated == orm


def test_the_orm_mirrors_the_new_fundamentals_columns():
    op = _run("upgrade")
    table = Base.metadata.tables["signals.fundamentals_snapshot"]
    for _, column in (c.args for c in op.add_column.call_args_list):
        orm = table.c[column.name]
        assert _type(orm) == _type(column), column.name
        assert orm.nullable is True and column.nullable is True


def test_filing_accession_is_unique_and_texts_cascade_with_their_filing():
    migrated = _migration_tables(_run("upgrade"))
    filings, texts = migrated["sec_filings"], migrated["sec_filing_texts"]
    unique = [c for c in filings.constraints if isinstance(c, sa.UniqueConstraint)]
    assert [(u.name, [col.name for col in u.columns]) for u in unique] == [
        ("uq_sec_filings_accession_number", ["accession_number"])
    ]
    [fk] = list(texts.foreign_key_constraints)
    assert fk.ondelete == "CASCADE"
    assert [e.target_fullname for e in fk.elements] == [
        "signals.sec_filings.accession_number"
    ]
    assert [c.name for c in texts.primary_key.columns] == [
        "accession_number",
        "exhibit",
    ]


def test_point_in_time_columns_are_timezone_aware_and_financials_are_per_ticker():
    migrated = _migration_tables(_run("upgrade"))
    filings = migrated["sec_filings"]
    assert filings.c.acceptance_datetime.type.timezone is True
    assert filings.c.acceptance_datetime.nullable is True
    assert filings.c.acceptance_raw.nullable is True
    assert filings.c.created_at.server_default is not None
    fin = migrated["financials_quarterly"]
    assert [c.name for c in fin.primary_key.columns] == ["ticker", "period_end"]
    assert (
        fin.c.filed.nullable is False
        and fin.c.filed.type.python_type.__name__ == "date"
    )
    assert fin.c.raw_tags.type.__class__.__name__ == "JSONB"
