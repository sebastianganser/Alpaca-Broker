"""Tests for scripts/repair/rebuild_sp500_membership.py (no DB, HTTP mocked).

Wikipedia is served from the trimmed fixtures in ``tests/fixtures`` (the same
tables the parser tests use); database sessions are fakes that record the
statements, so the diff / validation / manual-event logic and the order of the
writes are verified without a database.
"""

import csv
import importlib.util
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Select
from sqlalchemy.dialects import postgresql

from trading_signals.universe import wikipedia_index_history as w
from trading_signals.universe.wikipedia_index_history import (
    IndexChange,
    ParseResult,
    WikipediaIndexHistoryParser,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses with postponed annotations need this
    spec.loader.exec_module(mod)
    return mod


rb = _load("scripts/repair/rebuild_sp500_membership.py", "rebuild_sp500_membership")

START = date(2021, 1, 1)


def iv(ticker, valid_from, valid_to=None, source="wikipedia", reason=None, id=None):
    return rb.Interval(
        ticker=ticker,
        valid_from=valid_from,
        valid_to=valid_to,
        reason=reason,
        source=source,
        id=id,
    )


def change(ticker, action, day, reason=None, replaced_by=None, source="wikipedia"):
    return IndexChange(
        change_date=day,
        ticker=ticker,
        index_name="sp500",
        action=action,
        reason=reason,
        replaced_by=replaced_by,
        source=source,
    )


def parse_result(*changes, complete=True):
    return ParseResult(
        changes=list(changes),
        source="wikipedia_sp500",
        complete=complete,
        incomplete_reason=None if complete else "no removals",
        as_of=max((c.change_date for c in changes), default=None),
    )


def by_ticker(intervals):
    out = {}
    for i in intervals:
        out.setdefault(i.ticker, []).append((i.valid_from, i.valid_to))
    return out


# ── Arguments ────────────────────────────────────────────────────────────


class TestArgs:
    def test_dry_run_is_the_default(self):
        args = rb.parse_args([])
        assert args.apply is False
        assert args.restore is None
        assert args.backup_dir == "/mnt/user/Workfiles/AlpacaBroker/ops"
        assert args.max_deletes == 10

    def test_options(self):
        args = rb.parse_args(
            [
                "--apply",
                "--backup-dir",
                "/x",
                "--max-deletes",
                "3",
                "--restore",
                "f.csv",
            ]
        )
        assert args.apply and args.backup_dir == "/x"
        assert args.max_deletes == 3 and args.restore == "f.csv"


# ── Manual events (Paramount / Skydance) ─────────────────────────────────


class TestManualEvents:
    def test_constant_documents_para_exit_and_skyd_entry(self):
        events = {(e.ticker, e.action): e for e in rb.MANUAL_EVENTS}
        exit_ = events[("PARA", "removed")]
        entry = events[("SKYD", "added")]
        assert exit_.change_date == entry.change_date == date(2025, 8, 8)
        assert exit_.source == entry.source == "manual"
        assert exit_.index_name == entry.index_name == "sp500"
        assert exit_.replaced_by == "SKYD"
        assert ("PSKY", "added") not in events  # keyed by the CURRENT symbol

    def test_para_interval_ends_and_skyd_starts_on_the_merger_day(self):
        truth = rb.build_ground_truth(
            parse_result(change("ZZZ", "added", date(2023, 1, 3))),
            current={"AAA", "ZZZ", "SKYD"},
        )
        ivs = by_ticker(truth.intervals)
        assert ivs["PARA"] == [(START, date(2025, 8, 8))]
        assert ivs["SKYD"] == [(date(2025, 8, 8), None)]
        assert "PSKY" not in ivs
        assert truth.problems == []
        assert [e.ticker for e in truth.manual_applied] == ["PARA", "SKYD"]

    def test_constituent_list_with_the_old_symbol_is_rekeyed(self):
        truth = rb.build_ground_truth(
            parse_result(change("ZZZ", "added", date(2023, 1, 3))),
            current={"ZZZ", "PSKY"},
        )
        ivs = by_ticker(truth.intervals)
        assert ivs["SKYD"] == [(date(2025, 8, 8), None)]
        assert "PSKY" not in ivs
        assert any("PSKY" in n and "SKYD" in n for n in truth.notes)

    def test_manual_event_is_skipped_when_wikipedia_has_it(self):
        parsed = parse_result(
            change("PARA", "removed", date(2025, 8, 7)),
            change("SKYD", "added", date(2025, 8, 8)),
        )
        truth = rb.build_ground_truth(parsed, current={"SKYD"})
        assert truth.manual_applied == []
        assert {e.ticker for e, _ in truth.manual_skipped} == {"PARA", "SKYD"}
        assert by_ticker(truth.intervals)["PARA"] == [(START, date(2025, 8, 7))]
        assert by_ticker(truth.intervals)["SKYD"] == [(date(2025, 8, 8), None)]

    def test_manual_event_before_history_start_is_ignored(self):
        old = change("OLD", "removed", date(2020, 5, 1), source="manual")
        truth = rb.build_ground_truth(
            parse_result(), current={"AAA"}, manual_events=[old]
        )
        assert truth.manual_applied == [] and "OLD" not in by_ticker(truth.intervals)


# ── Ground truth from the Wikipedia fixtures ─────────────────────────────

#: members implied by the trimmed change-table fixture plus the manual SKYD entry
FIXTURE_CURRENT = {
    "TWLO", "VYLR", "BE", "P", "ILMN", "ECHO", "Q", "CVNA", "DAY", "SBNY",
    "FSLR", "WTW", "SKYD",
}  # fmt: skip


@pytest.fixture
def fixture_parse():
    html = (FIXTURES / "wikipedia_sp500_changes.html").read_text(encoding="utf-8")
    parser = WikipediaIndexHistoryParser()
    with patch.object(w, "fetch_html", return_value=html):
        return parser.parse_sp500_changes(since=START)


class TestGroundTruthFromFixture:
    def test_parse_is_complete(self, fixture_parse):
        assert fixture_parse.complete

    def test_rename_pairs_leave_one_continuous_interval_under_the_new_symbol(
        self, fixture_parse
    ):
        truth = rb.build_ground_truth(fixture_parse, FIXTURE_CURRENT)
        ivs = by_ticker(truth.intervals)
        assert truth.problems == []
        # CDAY -> DAY: added as CDAY 2021-09-20, one interval, no CDAY row
        assert ivs["DAY"] == [(date(2021, 9, 20), None)]
        # WLTW -> WTW: member before 2021, never closed, no WLTW row
        assert ivs["WTW"] == [(START, None)]
        # SATS -> ECHO: real addition 2026-03-23 under the current symbol
        assert ivs["ECHO"] == [(date(2026, 3, 23), None)]
        for old in ("CDAY", "WLTW", "SATS", "FBHS"):
            assert old not in ivs

    def test_real_exits_end_at_the_effective_date(self, fixture_parse):
        ivs = by_ticker(rb.build_ground_truth(fixture_parse, FIXTURE_CURRENT).intervals)
        assert ivs["TAP"] == [(START, date(2026, 9, 21))]
        assert ivs["TTD"] == [(START, date(2026, 9, 21))]
        assert ivs["BLDR"] == [(START, date(2026, 9, 21))]
        assert ivs["WBD"] == [(START, date(2026, 10, 6))]
        assert ivs["CTVA"] == [(START, date(2026, 10, 6))]
        # FBHS left on 2022-12-19 but is keyed by its current symbol FBIN
        assert ivs["FBIN"] == [(START, date(2022, 12, 19))]
        # four-day member
        assert ivs["MBC"] == [(date(2022, 12, 15), date(2022, 12, 19))]

    def test_current_members_without_event_start_at_history_start(self, fixture_parse):
        truth = rb.build_ground_truth(fixture_parse, FIXTURE_CURRENT | {"AAPL"})
        aapl = [i for i in truth.intervals if i.ticker == "AAPL"]
        assert [(i.valid_from, i.valid_to, i.source) for i in aapl] == [
            (START, None, "initial_seed")
        ]

    def test_every_interval_is_inside_the_allowed_range(self, fixture_parse):
        truth = rb.build_ground_truth(fixture_parse, FIXTURE_CURRENT)
        for i in truth.intervals:
            assert i.valid_from >= START
            assert i.valid_to is None or i.valid_to > i.valid_from

    def test_table_and_constituent_list_disagreeing_is_reported(self, fixture_parse):
        # TWLO was added by the table but is missing from the constituent list ...
        truth = rb.build_ground_truth(fixture_parse, FIXTURE_CURRENT - {"TWLO"})
        assert any("TWLO" in p and "not current" in p for p in truth.problems)
        # ... and WBD was removed by the table but the list still shows it
        truth = rb.build_ground_truth(fixture_parse, FIXTURE_CURRENT | {"WBD"})
        assert any("WBD" in p and "no open interval" in p for p in truth.problems)


# ── fetch_ground_truth: refuse unusable sources ──────────────────────────


class TestFetchGroundTruth:
    def test_incomplete_change_table_aborts_before_anything_else(self):
        parser = MagicMock()
        parser.parse_sp500_changes.return_value = parse_result(complete=False)
        with patch.object(rb, "fetch_current_sp500") as current:
            with pytest.raises(rb.AbortError, match="not complete"):
                rb.fetch_ground_truth(parser)
        current.assert_not_called()
        parser.parse_sp500_changes.assert_called_once_with(since=START)

    def test_implausible_constituent_count_aborts(self):
        parser = MagicMock()
        parser.parse_sp500_changes.return_value = parse_result(
            change("A", "removed", date(2022, 1, 3)),
            change("B", "added", date(2022, 1, 3)),
        )
        with patch.object(rb, "fetch_current_sp500", return_value={"A", "B"}):
            with pytest.raises(rb.AbortError, match="implausible"):
                rb.fetch_ground_truth(parser)

    def test_happy_path_builds_the_truth(self):
        parser = MagicMock()
        parser.parse_sp500_changes.return_value = parse_result(
            change("OLD", "removed", date(2022, 1, 3))
        )
        current = {f"T{i:03d}" for i in range(500)} | {"SKYD"}
        with patch.object(rb, "fetch_current_sp500", return_value=current):
            truth = rb.fetch_ground_truth(parser)
        assert truth.problems == []
        assert len([i for i in truth.intervals if i.valid_to is None]) == 501

    def test_fetch_current_sp500_reads_the_symbol_column(self):
        rows = "".join(f"<tr><td>T{i:03d}</td><td>x</td></tr>" for i in range(120))
        html = (
            "<table><tr><th>Symbol</th><th>Security</th></tr>"
            + rows
            + "<tr><td>BRK.B</td><td>b</td></tr></table>"
        )
        with patch.object(rb, "fetch_html", return_value=html):
            symbols = rb.fetch_current_sp500()
        assert len(symbols) == 121 and "BRK.B" in symbols and "T000" in symbols

    def test_fetch_current_sp500_without_table_aborts(self):
        with patch.object(
            rb, "fetch_html", return_value="<table><tr><th>A</th></tr></table>"
        ):
            with pytest.raises(rb.AbortError):
                rb.fetch_current_sp500()


# ── Diff ─────────────────────────────────────────────────────────────────


class TestDiff:
    def test_equal_sets_need_no_change(self):
        truth = [iv("A", START), iv("B", START, date(2022, 1, 3))]
        db = [iv("A", START, id=1), iv("B", START, date(2022, 1, 3), id=2)]
        plan = rb.diff_intervals(db, truth)
        assert plan.empty and plan.unchanged == 2

    def test_missing_interval_is_inserted(self):
        plan = rb.diff_intervals(
            [iv("A", START, id=1)],
            [iv("A", START), iv("KSU", START, date(2021, 12, 14))],
        )
        assert [(i.ticker, i.valid_from, i.valid_to) for i in plan.inserts] == [
            ("KSU", START, date(2021, 12, 14))
        ]
        assert not plan.updates and not plan.deletes

    def test_wrong_valid_to_is_updated(self):
        # TAP: closed with the sync date instead of the effective date
        db = [iv("TAP", START, date(2026, 10, 1), source="initial_seed", id=7)]
        truth = [iv("TAP", START, date(2026, 9, 21), reason="Market cap")]
        plan = rb.diff_intervals(db, truth)
        assert len(plan.updates) == 1 and not plan.inserts and not plan.deletes
        update = plan.updates[0]
        assert update.old.id == 7
        assert update.changes() == ["valid_to 2026-10-01 -> 2026-09-21"]

    def test_open_interval_that_must_be_closed_is_updated(self):
        plan = rb.diff_intervals(
            [iv("CTVA", START, None, id=1)], [iv("CTVA", START, date(2026, 10, 6))]
        )
        assert plan.updates[0].changes() == ["valid_to open -> 2026-10-06"]

    def test_wrong_valid_from_is_updated(self):
        # SW: seeded at the rename date, truth is the whole window
        plan = rb.diff_intervals(
            [iv("SW", date(2024, 7, 8), None, id=1)], [iv("SW", START, None)]
        )
        assert plan.updates[0].changes() == ["valid_from 2024-07-08 -> 2021-01-01"]
        assert not plan.inserts and not plan.deletes

    def test_re_entry_keeps_the_open_row_and_inserts_the_old_interval(self):
        # ILMN: left 2024-06-24, came back 2026-09-21; the DB only has the sync row
        db = [iv("ILMN", date(2026, 10, 1), None, source="index_sync", id=3)]
        truth = [
            iv("ILMN", START, date(2024, 6, 24)),
            iv("ILMN", date(2026, 9, 21), None),
        ]
        plan = rb.diff_intervals(db, truth)
        assert [u.changes() for u in plan.updates] == [
            ["valid_from 2026-10-01 -> 2026-09-21"]
        ]
        assert [(i.valid_from, i.valid_to) for i in plan.inserts] == [
            (START, date(2024, 6, 24))
        ]
        assert not plan.deletes

    def test_both_dates_wrong_but_overlapping_is_one_update(self):
        db = [iv("X", date(2022, 1, 3), date(2023, 1, 3), id=1)]
        truth = [iv("X", date(2021, 6, 1), date(2022, 6, 1))]
        plan = rb.diff_intervals(db, truth)
        assert len(plan.updates) == 1 and not plan.inserts and not plan.deletes
        assert len(plan.updates[0].changes()) == 2

    def test_row_that_is_not_in_the_truth_is_deleted(self):
        # PSKY from 2021-01-01 does not exist (the member is PARA, then SKYD)
        db = [
            iv("PSKY", START, None, source="initial_seed", id=9),
            iv("A", START, id=1),
        ]
        plan = rb.diff_intervals(db, [iv("A", START), iv("SKYD", date(2025, 8, 8))])
        assert [(d.ticker, d.id) for d in plan.deletes] == [("PSKY", 9)]
        assert [i.ticker for i in plan.inserts] == ["SKYD"]
        assert not plan.updates

    def test_disjoint_interval_of_the_same_ticker_is_delete_plus_insert(self):
        db = [iv("X", date(2021, 1, 1), date(2021, 6, 1), id=1)]
        truth = [iv("X", date(2023, 1, 3), date(2023, 6, 1))]
        plan = rb.diff_intervals(db, truth)
        assert len(plan.deletes) == 1 and len(plan.inserts) == 1 and not plan.updates

    def test_applying_the_plan_yields_an_empty_second_diff(self):
        truth = [
            iv("A", START),
            iv("TAP", START, date(2026, 9, 21)),
            iv("ILMN", START, date(2024, 6, 24)),
            iv("ILMN", date(2026, 9, 21)),
            iv("SKYD", date(2025, 8, 8)),
        ]
        db = [
            iv("A", START, id=1),
            iv("TAP", START, date(2026, 10, 1), id=2),
            iv("ILMN", date(2026, 10, 1), id=3),
            iv("PSKY", START, id=4),
        ]
        plan = rb.diff_intervals(db, truth)
        assert not plan.empty
        # emulate the writes
        state = {r.id: r for r in db}
        for d in plan.deletes:
            del state[d.id]
        for u in plan.updates:
            state[u.old.id] = iv(
                u.old.ticker, u.new.valid_from, u.new.valid_to, id=u.old.id
            )
        for n, i in enumerate(plan.inserts, start=100):
            state[n] = iv(i.ticker, i.valid_from, i.valid_to, id=n)
        again = rb.diff_intervals(list(state.values()), truth)
        assert again.empty and again.unchanged == len(truth)

    def test_format_prints_every_change_with_ticker_and_dates(self):
        db = [iv("TAP", START, date(2026, 10, 1), id=2), iv("PSKY", START, id=4)]
        truth = [
            iv("TAP", START, date(2026, 9, 21)),
            iv("KSU", START, date(2021, 12, 14)),
        ]
        text = "\n".join(rb.format_plan(rb.diff_intervals(db, truth)))
        assert "DELETE  PSKY" in text and "id=4" in text
        assert "UPDATE  TAP" in text and "valid_to 2026-10-01 -> 2026-09-21" in text
        assert "INSERT  KSU" in text and "2021-01-01 .. 2021-12-14" in text


# ── Point-in-time validation ─────────────────────────────────────────────


class TestPitValidation:
    DAYS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]

    def test_from_inclusive_to_exclusive_open_ends_never(self):
        ivs = [
            iv("A", date(2024, 1, 3), date(2024, 1, 5)),  # members on 3rd and 4th
            iv("B", date(2024, 1, 4)),  # open: from the 4th on
            iv(
                "C", date(2023, 1, 1), date(2024, 1, 2)
            ),  # first non-member day is the 2nd
        ]
        assert rb.pit_counts(ivs, self.DAYS) == [0, 1, 2, 1]

    def test_a_ticker_with_two_intervals_counts_once(self):
        ivs = [
            iv("A", date(2024, 1, 2), date(2024, 1, 4)),
            iv("A", date(2024, 1, 3)),  # overlapping duplicate
            iv("A", date(2023, 1, 1), date(2024, 1, 3)),
        ]
        assert rb.pit_counts(ivs, self.DAYS) == [1, 1, 1, 1]

    def test_re_entry_leaves_the_gap_uncounted(self):
        ivs = [
            iv("ILMN", START, date(2024, 1, 3)),
            iv("ILMN", date(2024, 1, 5)),
        ]
        assert rb.pit_counts(ivs, self.DAYS) == [1, 0, 0, 1]

    def test_report_year_starts_min_max_and_outside_band(self):
        sessions = self.DAYS
        before = [iv("A", date(2024, 1, 2))]
        after = [iv(f"T{i}", date(2024, 1, 2)) for i in range(5)] + [
            iv("X", date(2024, 1, 3), date(2024, 1, 5))
        ]
        rep = rb.build_pit_report(
            before, after, sessions, [date(2024, 1, 2)], band=(5, 6)
        )
        assert rep.year_starts == [(date(2024, 1, 2), 1, 5)]
        assert rep.before_min == (1, date(2024, 1, 2))
        assert rep.after_min == (5, date(2024, 1, 2))
        assert rep.after_max == (6, date(2024, 1, 3))  # earliest day of the maximum
        assert rep.outside_after == []
        assert rep.sessions == 4 and rep.outside_before == 4

    def test_sessions_outside_the_band_are_listed(self):
        after = [iv(f"T{i}", date(2024, 1, 2)) for i in range(10)]
        rep = rb.build_pit_report([], after, self.DAYS, [self.DAYS[0]], band=(495, 510))
        assert [d for d, _ in rep.outside_after] == self.DAYS
        text = "\n".join(rb.format_pit_report(rep))
        assert "SESSIONS OUTSIDE 495-510 AFTER REPAIR" in text
        assert "2024-01-02=10" in text

    def test_year_start_sessions_are_the_first_nyse_session(self):
        assert rb.year_start_sessions(2021, 2026) == [
            date(2021, 1, 4),
            date(2022, 1, 3),
            date(2023, 1, 3),
            date(2024, 1, 2),
            date(2025, 1, 2),
            date(2026, 1, 2),
        ]

    def test_truth_of_the_fixture_has_no_session_outside_the_plausible_band_logic(
        self, fixture_parse
    ):
        truth = rb.build_ground_truth(fixture_parse, FIXTURE_CURRENT)
        counts = rb.pit_counts(truth.intervals, [date(2022, 1, 3), date(2026, 10, 7)])
        assert counts[1] == len(FIXTURE_CURRENT)  # all current members on a recent day


# ── Database access (statements only) ────────────────────────────────────


def sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class FakeSession:
    """Records statements; SELECTs return ``rows`` (ORM-like objects)."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def execute(self, stmt, params=None):
        self.calls.append((stmt, params))
        result = MagicMock()
        if isinstance(stmt, Select):
            result.scalars.return_value.all.return_value = self.rows
        return result

    def flush(self):
        self.calls.append(("flush", None))


def orm_row(i: rb.Interval):
    return SimpleNamespace(
        id=i.id,
        ticker=i.ticker,
        valid_from=i.valid_from,
        valid_to=i.valid_to,
        reason=i.reason,
        replaced_by=i.replaced_by,
        source=i.source,
    )


class TestDatabase:
    def test_loader_only_reads_sp500_rows(self):
        session = FakeSession([orm_row(iv("A", START, id=1))])
        rows = rb.load_db_rows(session)
        assert [r.ticker for r in rows] == ["A"] and rows[0].id == 1
        text = sql(session.calls[0][0])
        assert "index_name = %(index_name_1)s" in text
        assert session.calls[0][0].compile().params["index_name_1"] == "sp500"

    def test_apply_orders_delete_update_insert_and_restricts_to_sp500(self):
        truth = [iv("A", START), iv("TAP", START, date(2026, 9, 21)), iv("NEW", START)]
        db = [
            iv("A", START, id=1),
            iv("TAP", START, date(2026, 10, 1), id=2),
            iv("PSKY", START, id=3),
        ]
        plan = rb.diff_intervals(db, truth)
        after = [iv("A", START, id=1), iv("TAP", START, date(2026, 9, 21), id=2),
                 iv("NEW", START, id=4)]  # fmt: skip
        session = FakeSession([orm_row(r) for r in after])
        rb.apply_plan(session, plan, truth)

        kinds = [type(c[0]).__name__ for c in session.calls if c[0] != "flush"]
        assert kinds == ["Delete", "Update", "Insert", "Select"]
        delete, update, insert, _ = (c[0] for c in session.calls[:4])
        for stmt in (delete, update):
            assert "index_name" in sql(stmt)
            assert stmt.compile().params["index_name_1"] == "sp500"
        assert delete.compile().params["id_1"] == [3]
        assert update.compile().params["id_1"] == 2
        assert update.compile().params["valid_to"] == date(2026, 9, 21)
        rows = session.calls[2][1]
        assert [(r["ticker"], r["index_name"]) for r in rows] == [("NEW", "sp500")]

    def test_post_check_mismatch_raises(self):
        truth = [iv("A", START), iv("NEW", START)]
        plan = rb.diff_intervals([iv("A", START, id=1)], truth)
        session = FakeSession(
            [orm_row(iv("A", START, id=1))]
        )  # insert "did not happen"
        with pytest.raises(RuntimeError, match="post-check failed"):
            rb.apply_plan(session, plan, truth)

    def test_long_reasons_are_clipped_to_the_column_length(self):
        truth = [iv("NEW", START, reason="x" * 500)]
        generated = [
            {
                "ticker": "NEW", "valid_from": START, "valid_to": None,
                "reason": "x" * 500, "replaced_by": None, "source": "wikipedia",
            }
        ]  # fmt: skip
        assert len(rb.to_intervals(generated)[0].reason) == rb.REASON_MAX
        assert len(truth[0].reason) == 500  # (the helper clips, not the dataclass)


# ── Orchestration ────────────────────────────────────────────────────────


def truth_with(*extra_intervals, n_open=500):
    """A self-consistent GroundTruth with ``n_open`` open members + ``extra``."""
    ivs = [iv(f"T{i:03d}", START) for i in range(n_open)] + list(extra_intervals)
    return rb.GroundTruth(
        intervals=ivs,
        current={i.ticker for i in ivs if i.valid_to is None},
        result=parse_result(change("X", "removed", date(2022, 1, 3))),
    )


@pytest.fixture
def harness(tmp_path):
    """Patches sessions and the ground truth; exposes recorded events."""
    state = SimpleNamespace(
        events=[], sessions=[], backup_dir=tmp_path, db_rows=[], truth=None,
        backup_seen_at_first_write=None,
    )  # fmt: skip

    @contextmanager
    def fake_repair_session(read_only):
        session = MagicMock()
        state.sessions.append((read_only, session))

        def execute(stmt, *a, **kw):
            text = str(stmt)
            if "LOCK TABLE" in text:
                state.events.append("lock")
            return MagicMock()

        session.execute.side_effect = execute
        session.commit.side_effect = lambda: state.events.append("commit")
        yield session

    def fake_apply(session, plan, truth):
        state.events.append("apply")
        state.backup_seen_at_first_write = sorted(
            p.name for p in tmp_path.glob("*.csv")
        )

    with (
        patch.object(rb, "repair_session", fake_repair_session),
        patch.object(rb, "announce_db_target"),
        patch.object(rb, "apply_plan", side_effect=fake_apply),
        patch.object(rb, "load_db_rows", side_effect=lambda s: state.db_rows),
        patch.object(rb, "fetch_ground_truth", side_effect=lambda: state.truth),
    ):
        yield state


class TestRun:
    def test_dry_run_is_read_only_and_writes_nothing(self, harness, capsys):
        harness.truth = truth_with(iv("KSU", START, date(2021, 12, 14)))
        harness.db_rows = [iv(f"T{i:03d}", START, id=i + 1) for i in range(500)]
        assert rb.main([]) == 0
        read_only, _ = harness.sessions[0]
        assert read_only is True
        assert "apply" not in harness.events and "commit" not in harness.events
        out = capsys.readouterr().out
        assert "DRY RUN" in out and "INSERT  KSU" in out
        assert "Point-in-time S&P 500 members" in out
        assert not list(harness.backup_dir.glob("*.csv"))

    def test_apply_locks_backs_up_everything_then_writes_once(self, harness, capsys):
        harness.truth = truth_with(iv("KSU", START, date(2021, 12, 14)))
        harness.db_rows = [iv(f"T{i:03d}", START, id=i + 1) for i in range(500)] + [
            iv("PSKY", START, source="initial_seed", id=501)
        ]
        rc = rb.main(["--apply", "--backup-dir", str(harness.backup_dir)])
        assert rc == 0
        assert harness.events == ["lock", "apply", "commit"]
        # the backup file existed when the first write started
        assert len(harness.backup_seen_at_first_write) == 1
        path = harness.backup_dir / harness.backup_seen_at_first_write[0]
        assert path.name.startswith("sp500_membership_backup_")
        with path.open(newline="", encoding="utf-8") as fh:
            saved = list(csv.DictReader(fh))
        assert len(saved) == 501  # ALL old rows, also the unchanged ones
        assert list(saved[0]) == list(rb.BACKUP_COLUMNS)
        assert {r["index_name"] for r in saved} == {"sp500"}
        assert any(r["ticker"] == "PSKY" and r["id"] == "501" for r in saved)
        assert "--restore" in capsys.readouterr().out
        read_only, _ = harness.sessions[0]
        assert read_only is False

    def test_second_apply_changes_nothing_and_writes_no_backup(self, harness, capsys):
        harness.truth = truth_with(iv("KSU", START, date(2021, 12, 14)))
        harness.db_rows = [
            iv(i.ticker, i.valid_from, i.valid_to, id=n)
            for n, i in enumerate(harness.truth.intervals, 1)
        ]
        rc = rb.main(["--apply", "--backup-dir", str(harness.backup_dir)])
        assert rc == 0
        assert "apply" not in harness.events and "commit" not in harness.events
        assert not list(harness.backup_dir.glob("*.csv"))
        assert "Nothing to change" in capsys.readouterr().out

    def test_too_many_deletes_abort_before_backup_and_writes(self, harness, capsys):
        harness.truth = truth_with()
        harness.db_rows = [iv(f"T{i:03d}", START, id=i + 1) for i in range(500)] + [
            iv(f"BAD{i}", START, id=1000 + i) for i in range(11)
        ]
        rc = rb.main(["--apply", "--backup-dir", str(harness.backup_dir)])
        assert rc == 2
        assert "apply" not in harness.events and not list(
            harness.backup_dir.glob("*.csv")
        )
        assert "--max-deletes" in capsys.readouterr().out
        # an explicit higher limit lets it through
        rc = rb.main(
            ["--apply", "--backup-dir", str(harness.backup_dir), "--max-deletes", "20"]
        )
        assert rc == 0 and "apply" in harness.events

    def test_member_counts_outside_the_band_block_apply(self, harness, capsys):
        harness.truth = truth_with(n_open=20)  # 20 members: far outside 495-510
        harness.db_rows = []
        rc = rb.main(["--apply", "--backup-dir", str(harness.backup_dir)])
        assert rc == 2
        assert "495-510" in capsys.readouterr().out
        assert "apply" not in harness.events
        rc = rb.main(
            ["--apply", "--backup-dir", str(harness.backup_dir), "--ignore-validation"]
        )
        assert rc == 0 and "apply" in harness.events

    def test_unwritable_backup_dir_aborts_without_writing(
        self, harness, tmp_path, capsys
    ):
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        harness.truth = truth_with(iv("KSU", START, date(2021, 12, 14)))
        harness.db_rows = [iv(f"T{i:03d}", START, id=i + 1) for i in range(500)]
        rc = rb.main(["--apply", "--backup-dir", str(blocker / "ops")])
        assert rc == 2
        assert "apply" not in harness.events and "commit" not in harness.events
        assert "cannot write the backup" in capsys.readouterr().out

    def test_inconsistent_truth_aborts(self, harness, capsys):
        truth = truth_with()
        truth.problems = ["1 current constituents have no open interval"]
        harness.truth = truth
        assert rb.main(["--apply"]) == 2
        out = capsys.readouterr().out
        assert "PROBLEM" in out and "not self-consistent" in out
        assert not harness.sessions  # never even opened a DB session

    def test_incomplete_source_aborts_with_exit_2(self, capsys):
        with (
            patch.object(rb, "announce_db_target"),
            patch.object(
                rb, "fetch_ground_truth", side_effect=rb.AbortError("no removals")
            ),
        ):
            assert rb.main(["--apply"]) == 2
        assert "ABORT: no removals" in capsys.readouterr().out


class TestRestore:
    def _backup(self, tmp_path, rows):
        return rb.write_csv_backup(rows, rb.BACKUP_COLUMNS, tmp_path, "bk")

    def test_restore_plan_brings_back_the_backed_up_rows(
        self, harness, tmp_path, capsys
    ):
        backup = self._backup(
            tmp_path,
            rb.backup_rows(
                [iv("PSKY", START, source="initial_seed", id=1), iv("A", START, id=2)]
            ),
        )
        # DB after the repair: PSKY gone, many new rows (more than --max-deletes)
        harness.db_rows = [iv("A", START, id=2)] + [
            iv(f"NEW{i}", START, date(2022, 1, 3), id=10 + i) for i in range(30)
        ]
        rc = rb.main(
            ["--restore", str(backup), "--apply", "--backup-dir", str(tmp_path / "ops")]
        )
        assert rc == 0
        assert harness.events == ["lock", "apply", "commit"]
        # the CURRENT state is backed up before it is replaced
        assert len(list((tmp_path / "ops").glob("*.csv"))) == 1
        assert "Restoring the sp500 rows" in capsys.readouterr().out

    def test_restore_of_a_foreign_file_aborts(self, harness, tmp_path, capsys):
        bad = tmp_path / "bad.csv"
        bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
        assert rb.main(["--restore", str(bad), "--apply"]) == 2
        assert "not a membership backup" in capsys.readouterr().out
        assert "apply" not in harness.events

    def test_backup_rows_cover_every_column(self):
        rows = rb.backup_rows([iv("A", START, date(2022, 1, 3), reason="r", id=5)])
        assert set(rows[0]) == set(rb.BACKUP_COLUMNS)
        assert rows[0]["id"] == 5 and rows[0]["index_name"] == "sp500"
