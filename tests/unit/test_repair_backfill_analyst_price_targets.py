"""Tests for scripts/repair/backfill_analyst_price_targets.py (mock session, no DB)."""

from __future__ import annotations

import importlib.util
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bp = _load(
    "scripts/repair/backfill_analyst_price_targets.py",
    "backfill_analyst_price_targets",
)


def sql_of(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


# ── arguments ─────────────────────────────────────────────────────────


def test_dry_run_is_the_default():
    args = bp.parse_args([])
    assert args.apply is False and args.batch == bp.DEFAULT_BATCH
    assert bp.parse_args(["--apply", "--batch", "50"]).apply is True
    assert bp.parse_args(["--batch", "50"]).batch == 50


# ── planning one row ──────────────────────────────────────────────────


def test_a_null_column_is_filled_from_the_raw_data_text():
    fill = bp.plan_row(7, None, None, "275.0", "250.0")
    assert fill == bp.Fill(7, 275.0, 250.0)


def test_a_stored_value_is_never_replaced():
    # new is stored, old is NULL: only old is filled, new stays untouched
    assert bp.plan_row(1, 111.0, None, "275.0", "250.0") == bp.Fill(1, None, 250.0)
    assert bp.plan_row(2, None, 99.0, "275.0", "250.0") == bp.Fill(2, 275.0, None)
    # both stored: nothing to do, whatever raw_data says
    assert bp.plan_row(3, 111.0, 99.0, "275.0", "250.0") is None


def test_no_target_in_raw_data_leaves_the_column_null_never_zero():
    for raw in (None, "nan", "NaN", "None", "", "0.0", "0", "-3", "abc", "1e12"):
        assert bp.plan_row(1, None, None, raw, raw) is None, raw
    # one usable end is enough to write that end only
    assert bp.plan_row(1, None, None, "140.0", "0.0") == bp.Fill(1, 140.0, None)
    assert bp.plan_row(1, None, None, "nan", "120.5") == bp.Fill(1, None, 120.5)


def test_missing_rows_are_classified_by_why_nothing_is_written():
    assert bp.classify_missing(None, None, None, None) == "no_raw"
    assert bp.classify_missing(None, None, "nan", None) == "unusable"
    assert bp.classify_missing(None, None, "0.0", "None") == "unusable"
    assert bp.classify_missing(None, None, "100", None) is None  # fillable
    assert bp.classify_missing(5.0, 6.0, None, None) is None  # not a gap
    # only the NULL column is looked at
    assert bp.classify_missing(5.0, None, "100", None) == "no_raw"
    assert bp.classify_missing(5.0, None, "100", "nan") == "unusable"


def test_a_batch_is_counted_by_outcome():
    stats = bp.Stats()
    rows = [
        (1, None, None, "275.0", "250.0"),  # both filled
        (2, None, None, "140.0", "nan"),  # only new
        (3, 90.0, None, "100.0", "80.0"),  # only old (new is stored)
        (4, None, None, None, None),  # no raw target key
        (5, None, None, "nan", "0.0"),  # key present, no usable value
        (6, 50.0, None, "99.0", None),  # old has no raw value
    ]
    fills = bp.plan_batch(rows, stats)
    assert [f.row_id for f in fills] == [1, 2, 3]
    assert stats.scanned == 6 and stats.rows_to_update == 3
    assert (stats.fill_new, stats.fill_old) == (2, 2)
    assert stats.no_raw_target == 2 and stats.unusable_raw_target == 1
    assert len(stats.samples) == 3 and stats.samples[0] == (1, 275.0, 250.0)


# ── SQL ───────────────────────────────────────────────────────────────


def test_the_update_only_fills_nulls_and_is_one_statement_per_chunk():
    fills = [bp.Fill(1, 275.0, 250.0), bp.Fill(2, None, 120.5)]
    sql = sql_of(bp.build_update(fills))
    assert sql.startswith("UPDATE signals.analyst_ratings SET")
    # a value that is already there always wins, even if the collector wrote it
    # between the read and the write
    assert "coalesce(signals.analyst_ratings.price_target_new" in sql
    assert "coalesce(signals.analyst_ratings.price_target_old" in sql
    assert "FROM (VALUES" in sql and "analyst_ratings.id = v.id" in sql
    assert "raw_data" not in sql.split("SET", 1)[1]  # raw_data is never modified


def test_the_scan_selects_only_rows_with_a_null_column_in_id_order():
    session = MagicMock()
    session.execute.return_value.all.return_value = []
    assert bp.fetch_batch(session, 41, 500) == []
    statement = session.execute.call_args.args[0]
    sql = sql_of(statement)
    assert (
        "analyst_ratings.id >" in sql and "ORDER BY signals.analyst_ratings.id" in sql
    )
    assert "price_target_new IS NULL OR" in sql and "price_target_old IS NULL" in sql
    assert "LIMIT" in sql
    keys = {str(v) for v in statement.compile().params.values()}
    assert {"currentPriceTarget", "priorPriceTarget"} <= keys
    assert 41 in statement.compile().params.values()


# ── the whole script ──────────────────────────────────────────────────


BEFORE = {"rows": 10, "new_set": 0, "old_set": 0, "raw_new_key": 9, "raw_old_key": 8}
AFTER = {"rows": 10, "new_set": 8, "old_set": 7, "raw_new_key": 9, "raw_old_key": 8}


class Harness:
    """Runs ``main`` against canned batches and records what it would write."""

    def __init__(self, batches, counts=(BEFORE, AFTER)):
        self.batches = list(batches)
        self.counts = list(counts)
        self.session = MagicMock()
        self.sessions_opened: list[bool] = []  # read_only flag of each session
        self.fetch_calls: list[tuple[int, int]] = []
        self.applied: list[list] = []

    @contextmanager
    def repair_session(self, read_only):
        self.sessions_opened.append(read_only)
        yield self.session

    def fetch_batch(self, session, last_id, limit):
        self.fetch_calls.append((last_id, limit))
        return self.batches.pop(0) if self.batches else []

    def column_counts(self, session):
        return self.counts.pop(0)

    def apply_fills(self, session, fills):
        self.applied.append(list(fills))

    def main(self, argv, capsys=None):
        with (
            patch.object(bp, "repair_session", self.repair_session),
            patch.object(bp, "announce_db_target"),
            patch.object(bp, "setup_logging"),
            patch.object(bp, "fetch_batch", self.fetch_batch),
            patch.object(bp, "column_counts", self.column_counts),
            patch.object(bp, "apply_fills", self.apply_fills),
        ):
            return bp.main(argv)


FIRST = [(1, None, None, "275.0", "250.0"), (2, None, None, "nan", "0.0")]
SECOND = [(3, None, None, "140.0", None)]


def test_dry_run_reads_in_a_read_only_session_and_writes_nothing(capsys):
    h = Harness([FIRST, SECOND], counts=(BEFORE,))
    assert h.main([]) == 0
    assert h.sessions_opened == [True]  # SET TRANSACTION READ ONLY
    assert h.applied == []
    h.session.commit.assert_not_called()
    h.session.execute.assert_not_called()
    out = capsys.readouterr().out
    assert "mode: DRY RUN (read-only)" in out
    assert "rows to update:" in out and "DRY RUN -- nothing written" in out
    assert re.search(r"price_target_new filled:\s+2\n", out)
    assert re.search(r"key present, value unusable \(0 / nan\):\s+1\n", out)
    # the scan is keyed by id and moves on after every batch
    assert h.fetch_calls == [
        (0, bp.DEFAULT_BATCH),
        (2, bp.DEFAULT_BATCH),
        (3, bp.DEFAULT_BATCH),
    ]


def test_apply_writes_each_batch_and_commits_per_batch(capsys):
    h = Harness([FIRST, SECOND])
    assert h.main(["--apply", "--batch", "2"]) == 0
    assert h.sessions_opened == [False]
    assert [[f.row_id for f in batch] for batch in h.applied] == [[1], [3]]
    assert h.session.commit.call_count == 2  # one commit per batch
    out = capsys.readouterr().out
    assert "mode: APPLY" in out and "after" in out
    assert "DRY RUN" not in out


def test_a_second_run_finds_nothing_to_change(capsys):
    done = {"rows": 10, "new_set": 8, "old_set": 7, "raw_new_key": 9, "raw_old_key": 8}
    h = Harness([], counts=(done, done))  # nothing with a NULL column is left to scan
    assert h.main(["--apply"]) == 0
    assert h.applied == [] and h.session.commit.call_count == 0
    assert "rows updated:" in capsys.readouterr().out
    assert h.fetch_calls == [(0, bp.DEFAULT_BATCH)]


def test_a_batch_without_any_fillable_row_is_not_written(capsys):
    h = Harness([[(1, None, None, "nan", None)]])
    assert h.main(["--apply"]) == 0
    assert h.applied == [] and h.session.commit.call_count == 0


def test_update_chunking_splits_large_batches():
    session = MagicMock()
    fills = [bp.Fill(i, 1.0, None) for i in range(bp.UPDATE_CHUNK * 2 + 5)]
    bp.apply_fills(session, fills)
    assert session.execute.call_count == 3  # 1000 + 1000 + 5 rows
