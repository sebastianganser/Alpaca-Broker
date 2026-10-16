"""Tests for scripts/repair/onboard_removed_members.py (no DB, HTTP mocked)."""

import importlib.util
import io
import sys
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests
from sqlalchemy import Select
from sqlalchemy.dialects import postgresql

from trading_signals.universe.ticker_aliases import AliasMap, TickerAlias

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ob = _load("scripts/repair/onboard_removed_members.py", "onboard_removed_members")

DS = date(2021, 10, 1)
LAST = date(2026, 10, 15)


def dt(text: str) -> date:
    return date.fromisoformat(text)


def bar(day: str, close: float = 10.0, volume: int = 1000) -> dict:
    return {
        "t": f"{day}T04:00:00Z",
        "o": close,
        "h": close + 1,
        "l": close - 1,
        "c": close,
        "v": volume,
    }


def prow(day: str, close: float = 10.0, ticker: str = "T") -> dict:
    return {
        "ticker": ticker,
        "trade_date": dt(day),
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "adj_close": close,
        "volume": 1000,
        "source": "alpaca_backfill",
        "is_extrapolated": False,
    }


def stored(close: float = 10.0, extrapolated: bool = False, volume: int = 1000):
    return ob.StoredPrice(close, close + 1, close - 1, close, volume, extrapolated)


# ── Arguments ────────────────────────────────────────────────────────────


class TestArgs:
    def test_dry_run_is_the_default(self):
        args = ob.parse_args([])
        assert args.apply is False and args.fix_para is False
        assert args.no_fetch is False and args.add_current_members is False
        assert args.asof == "first" and args.feed is None
        assert args.backup_dir == "/mnt/user/Workfiles/AlpacaBroker/ops"

    def test_options(self):
        args = ob.parse_args(
            ["--apply", "--fix-para", "--tickers", "DAY,INFO", "--asof", "last",
             "--feed", "iex", "--no-fetch", "--add-current-members"]
        )  # fmt: skip
        assert args.apply and args.fix_para and args.no_fetch
        assert args.tickers == "DAY,INFO" and args.asof == "last"
        assert args.feed == "iex" and args.add_current_members


# ── Membership ranges and expected sessions ──────────────────────────────


class TestMemberRange:
    def test_valid_to_is_the_first_non_member_day(self):
        # removal effective 2021-12-14 -> last member day 2021-12-13
        assert ob.member_range((dt("2021-01-01"), dt("2021-12-14")), DS, LAST) == (
            DS,
            dt("2021-12-13"),
        )

    def test_open_interval_runs_to_the_last_session(self):
        assert ob.member_range((dt("2023-05-01"), None), DS, LAST) == (
            dt("2023-05-01"),
            LAST,
        )

    def test_interval_before_the_window_has_no_member_days(self):
        assert ob.member_range((dt("2021-01-01"), dt("2021-09-20")), DS, LAST) is None
        assert ob.member_range((dt("2021-01-01"), DS), DS, LAST) is None

    def test_window_intervals_drop_what_ends_before_the_window(self):
        data = {
            "OLD": [(dt("2021-01-01"), dt("2021-09-20"))],  # TIF-like pre-window exit
            "KSU": [(dt("2021-01-01"), dt("2021-12-14"))],
            "ILMN": [(dt("2021-01-01"), dt("2024-06-24")), (dt("2026-09-21"), None)],
        }
        kept = ob.window_intervals(data, DS)
        assert set(kept) == {"KSU", "ILMN"}


class TestSessions:
    def test_calendar_slice_skips_weekends(self):
        cal = ob.SessionCalendar(dt("2025-08-01"), dt("2025-08-31"))
        days = cal.between(dt("2025-08-04"), dt("2025-08-12"))
        assert days[0] == dt("2025-08-04") and days[-1] == dt("2025-08-12")
        assert dt("2025-08-09") not in days and dt("2025-08-10") not in days
        assert len(days) == 7

    def test_expected_sessions_use_the_membership_interval(self):
        cal = ob.SessionCalendar(dt("2025-08-01"), dt("2025-08-31"))
        # member through 2025-08-07 (valid_to = 08-08 is the first non-member day)
        got = ob.expected_sessions(
            [(dt("2025-07-01"), dt("2025-08-08"))],
            cal,
            dt("2025-08-04"),
            dt("2025-08-29"),
        )
        assert got == [
            dt("2025-08-04"),
            dt("2025-08-05"),
            dt("2025-08-06"),
            dt("2025-08-07"),
        ]

    def test_two_intervals_are_united(self):
        cal = ob.SessionCalendar(dt("2025-08-01"), dt("2025-08-31"))
        ivs = [
            (dt("2025-08-04"), dt("2025-08-06")),
            (dt("2025-08-12"), dt("2025-08-14")),
        ]
        got = ob.expected_sessions(ivs, cal, dt("2025-08-01"), dt("2025-08-31"))
        assert got == [
            dt("2025-08-04"),
            dt("2025-08-05"),
            dt("2025-08-12"),
            dt("2025-08-13"),
        ]

    def test_need_range_is_window_start_to_last_member_day(self):
        ivs = [(dt("2021-01-01"), dt("2024-06-24")), (dt("2026-09-21"), None)]
        assert ob.need_range(ivs, DS, LAST) == (DS, LAST, DS)
        assert ob.need_range([(dt("2023-12-18"), dt("2026-09-21"))], DS, LAST) == (
            DS,
            dt("2026-09-20"),
            dt("2023-12-18"),
        )
        assert ob.need_range([(dt("2020-01-01"), dt("2021-06-01"))], DS, LAST) is None


# ── Fetch plan: symbol history, asof ─────────────────────────────────────


class TestFetchTasks:
    def test_plain_ticker_is_one_request_from_window_start(self):
        tasks = ob.build_fetch_tasks("KSU", DS, dt("2021-12-13"), DS)
        assert tasks == [ob.FetchTask("KSU", "KSU", DS, dt("2021-12-13"), asof=DS)]

    def test_renamed_ticker_fetches_each_symbol_for_its_own_period(self):
        # DAY: Alpaca serves the days before 2024-02-01 only under CDAY
        tasks = ob.build_fetch_tasks("DAY", DS, dt("2026-02-08"), DS)
        assert [(t.store_ticker, t.symbol, t.start, t.end) for t in tasks] == [
            ("DAY", "CDAY", DS, dt("2024-01-31")),
            ("DAY", "DAY", dt("2024-02-01"), dt("2026-02-08")),
        ]  # everything is stored under the CURRENT symbol

    def test_paramount_uses_viac_and_para_periods(self):
        tasks = ob.build_fetch_tasks("PARA", DS, dt("2025-08-07"), DS)
        assert [(t.symbol, t.start, t.end) for t in tasks] == [
            ("VIAC", DS, dt("2022-02-16")),
            ("PARA", dt("2022-02-17"), dt("2025-08-07")),
        ]
        assert {t.store_ticker for t in tasks} == {"PARA"}

    def test_open_member_after_a_rename_uses_the_old_and_new_symbol(self):
        tasks = ob.build_fetch_tasks("SKYD", DS, LAST, dt("2025-08-08"))
        assert [(t.symbol, t.start, t.end) for t in tasks] == [
            ("PSKY", DS, dt("2026-10-05")),
            ("SKYD", dt("2026-10-06"), LAST),
        ]

    def test_period_entirely_before_the_range_is_skipped(self):
        # need_from after the first symbol's period -> only the second request
        tasks = ob.build_fetch_tasks(
            "DAY", dt("2024-06-01"), dt("2025-01-31"), dt("2024-06-01")
        )
        assert [(t.symbol, t.start) for t in tasks] == [("DAY", dt("2024-06-01"))]

    def test_unknown_symbol_gets_a_single_open_period(self):
        amap = AliasMap([TickerAlias("OLD", "NEW", dt("2023-01-02"))])
        assert len(ob.build_fetch_tasks("XYZ", DS, LAST, DS, alias_map=amap)) == 1
        assert len(ob.build_fetch_tasks("NEW", DS, LAST, DS, alias_map=amap)) == 2


class TestAsof:
    def test_first_mode_uses_the_first_member_day_of_the_request(self):
        # INFO: member before the window, IHS Markit last traded 2022-02-25,
        # removal 2022-03-02 -> asof must NOT be the last membership day
        (task,) = ob.build_fetch_tasks("INFO", DS, dt("2022-03-01"), DS, "first")
        assert task.asof == DS

    def test_last_mode_is_the_literal_last_membership_day(self):
        (task,) = ob.build_fetch_tasks("INFO", DS, dt("2022-03-01"), DS, "last")
        assert task.asof == dt("2022-03-01")

    def test_late_listed_member_uses_its_membership_start(self):
        # MBC: listed and added 2022-12-15, the symbol did not exist in 2021
        (task,) = ob.build_fetch_tasks("MBC", DS, dt("2022-12-18"), dt("2022-12-15"))
        assert task.asof == dt("2022-12-15")

    def test_asof_stays_inside_the_period_of_each_symbol(self):
        tasks = ob.build_fetch_tasks("P", DS, LAST, dt("2026-09-21"))
        pstg, p = tasks
        assert pstg.symbol == "PSTG" and pstg.asof == dt("2026-04-16")  # clamped to end
        assert p.symbol == "P" and p.asof == dt("2026-09-21")  # first member day
        for t in tasks:
            assert t.start <= t.asof <= t.end

    def test_para_asof_resolves_the_old_entity(self):
        tasks = ob.build_fetch_tasks("PARA", DS, dt("2025-08-07"), DS, "first")
        assert [t.asof for t in tasks] == [DS, dt("2022-02-17")]
        last = ob.build_fetch_tasks("PARA", DS, dt("2025-08-07"), DS, "last")
        assert last[-1].asof == dt("2025-08-07")  # the value named in the brief


class TestGrouping:
    def test_tasks_with_identical_range_and_asof_share_a_request(self):
        a = ob.FetchTask("A", "A", DS, dt("2021-12-17"), DS)
        b = ob.FetchTask("B", "B", DS, dt("2021-12-17"), DS)
        c = ob.FetchTask("C", "C", DS, dt("2022-01-04"), DS)
        groups = ob.group_tasks([c, b, a])
        assert [[t.symbol for t in g] for g in groups] == [["A", "B"], ["C"]]

    def test_groups_are_chunked(self):
        tasks = [ob.FetchTask(f"T{i}", f"T{i}", DS, LAST, DS) for i in range(5)]
        assert [len(g) for g in ob.group_tasks(tasks, size=2)] == [2, 2, 1]


# ── HTTP ─────────────────────────────────────────────────────────────────

HEADERS = {"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s"}


def response(payload=None, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload or {}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"HTTP {status}", response=resp
        )
    return resp


@pytest.fixture
def no_sleep():
    with (
        patch.object(ob.time, "sleep"),
        patch("trading_signals.utils.retry.time.sleep"),
    ):
        yield


class TestFetchBars:
    def test_request_parameters_match_the_collector_plus_asof(self):
        with patch.object(
            ob.requests,
            "get",
            return_value=response({"bars": {"INFO": [bar("2021-10-01")]}}),
        ) as get:
            bars = ob.fetch_bars(["INFO"], DS, dt("2022-03-01"), DS, HEADERS, "sip")
        assert bars == {"INFO": [bar("2021-10-01")]}
        (url,), kwargs = get.call_args
        assert url == "https://data.alpaca.markets/v2/stocks/bars"
        assert kwargs["headers"] == HEADERS
        params = kwargs["params"]
        assert params == {
            "symbols": "INFO",
            "timeframe": "1Day",
            "start": "2021-10-01T00:00:00Z",
            "end": "2022-03-01T23:59:59Z",  # the end day itself is included
            "limit": 10000,
            "adjustment": "all",
            "feed": "sip",
            "asof": "2021-10-01",
        }

    def test_pagination_collects_all_pages(self):
        pages = [
            response({"bars": {"A": [bar("2021-10-01")]}, "next_page_token": "tok"}),
            response({"bars": {"A": [bar("2021-10-04")]}}),
        ]
        with patch.object(ob.requests, "get", side_effect=pages) as get:
            bars = ob.fetch_bars(["A"], DS, LAST, DS, HEADERS, "sip")
        assert [b["t"][:10] for b in bars["A"]] == ["2021-10-01", "2021-10-04"]
        assert get.call_args_list[1].kwargs["params"]["page_token"] == "tok"

    def test_null_bars_payload_is_empty(self):
        with patch.object(ob.requests, "get", return_value=response({"bars": None})):
            assert ob.fetch_bars(["A"], DS, LAST, DS, HEADERS, "sip") == {}


class TestFetchTaskBars:
    def test_bars_are_mapped_back_to_their_task(self, no_sleep):
        t1 = ob.FetchTask("DAY", "CDAY", DS, dt("2024-01-31"), DS)
        t2 = ob.FetchTask(
            "DAY", "DAY", dt("2024-02-01"), dt("2026-02-08"), dt("2024-02-01")
        )
        payloads = {
            "CDAY": {"bars": {"CDAY": [bar("2021-10-01")]}},
            "DAY": {"bars": {"DAY": [bar("2024-02-01")]}},
        }

        def get(url, headers, params, timeout):
            return response(payloads[params["symbols"]])

        with patch.object(ob.requests, "get", side_effect=get) as mocked:
            result, errors = ob.fetch_task_bars([t1, t2], HEADERS, "sip")
        assert errors == [] and mocked.call_count == 2
        assert result[t1][0]["t"].startswith("2021-10-01")
        assert result[t2][0]["t"].startswith("2024-02-01")

    def test_http_error_is_reported_and_other_requests_continue(self, no_sleep):
        bad = ob.FetchTask("X", "X", DS, LAST, DS)
        good = ob.FetchTask("Y", "Y", dt("2022-01-03"), LAST, dt("2022-01-03"))

        def get(url, headers, params, timeout):
            if params["symbols"] == "X":
                return response(status=400)
            return response({"bars": {"Y": [bar("2022-01-03")]}})

        with patch.object(ob.requests, "get", side_effect=get):
            result, errors = ob.fetch_task_bars([bad, good], HEADERS, "sip")
        assert result[bad] == [] and len(result[good]) == 1
        assert len(errors) == 1 and "X" in errors[0] and "HTTP 400" in errors[0]

    def test_subscription_error_aborts_without_iex_fallback(self, no_sleep):
        task = ob.FetchTask("X", "X", DS, LAST, DS)
        with patch.object(ob.requests, "get", return_value=response(status=403)):
            with pytest.raises(ob.FeedError, match="does not fall back"):
                ob.fetch_task_bars([task], HEADERS, "sip")

    def test_transient_errors_are_retried(self, no_sleep):
        task = ob.FetchTask("X", "X", DS, LAST, DS)
        replies = [response(status=503), response({"bars": {"X": [bar("2021-10-01")]}})]
        with patch.object(ob.requests, "get", side_effect=replies):
            result, errors = ob.fetch_task_bars([task], HEADERS, "sip")
        assert errors == [] and len(result[task]) == 1


class TestTaskRows:
    def test_rows_are_stored_under_the_current_symbol_with_backfill_source(self):
        task = ob.FetchTask("DAY", "CDAY", dt("2021-10-01"), dt("2021-10-05"), DS)
        rows = ob.task_rows(task, [bar("2021-10-01"), bar("2021-10-04")])
        assert {r["ticker"] for r in rows} == {"DAY"}
        assert {r["source"] for r in rows} == {"alpaca_backfill"}
        assert all(r["is_extrapolated"] is False for r in rows)
        assert all(r["adj_close"] == r["close"] for r in rows)

    def test_bars_outside_the_requested_range_are_dropped(self):
        task = ob.FetchTask("X", "X", dt("2021-10-04"), dt("2021-10-05"), DS)
        days = ["2021-10-01", "2021-10-04", "2021-10-05", "2021-10-06"]
        rows = ob.task_rows(task, [bar(d) for d in days])
        assert [r["trade_date"] for r in rows] == [dt("2021-10-04"), dt("2021-10-05")]

    def test_never_before_the_requested_start_which_is_the_retention_start(self):
        tasks = ob.build_fetch_tasks("KSU", DS, dt("2021-12-13"), DS)
        assert all(t.start >= DS for t in tasks)


# ── Writing only what changes ────────────────────────────────────────────


class TestRowsToWrite:
    def test_new_rows_are_written(self):
        assert ob.rows_to_write([prow("2022-01-03")], {}) == [prow("2022-01-03")]

    def test_identical_rows_are_skipped(self):
        rows = [prow("2022-01-03", 10.0)]
        assert ob.rows_to_write(rows, {dt("2022-01-03"): stored(10.0)}) == []

    def test_float_noise_below_the_column_precision_counts_as_equal(self):
        rows = [prow("2022-01-03", 10.00001)]
        assert ob.rows_to_write(rows, {dt("2022-01-03"): stored(10.0)}) == []

    def test_changed_value_is_written(self):
        rows = [prow("2022-01-03", 10.5)]
        assert ob.rows_to_write(rows, {dt("2022-01-03"): stored(10.0)}) == rows

    def test_changed_volume_is_written(self):
        rows = [prow("2022-01-03", 10.0)]
        assert (
            ob.rows_to_write(rows, {dt("2022-01-03"): stored(10.0, volume=5)}) == rows
        )

    def test_gap_filler_is_replaced_by_the_real_bar(self):
        rows = [prow("2022-01-03", 10.0)]
        stored_rows = {dt("2022-01-03"): stored(10.0, extrapolated=True)}
        assert ob.rows_to_write(rows, stored_rows) == rows


class TestGaps:
    EXPECTED = [dt("2022-01-03"), dt("2022-01-04"), dt("2022-01-05"), dt("2022-01-06")]

    def test_no_gap(self):
        assert ob.classify_gaps(self.EXPECTED, set(self.EXPECTED)) == {
            "head": [], "internal": [], "tail": []
        }  # fmt: skip

    def test_tail_head_and_internal(self):
        have = {dt("2022-01-04"), dt("2022-01-06")}
        gaps = ob.classify_gaps(self.EXPECTED, have)
        assert gaps["head"] == [dt("2022-01-03")]
        assert gaps["internal"] == [dt("2022-01-05")]
        assert gaps["tail"] == []
        gaps = ob.classify_gaps(self.EXPECTED, {dt("2022-01-03"), dt("2022-01-04")})
        assert gaps["tail"] == [dt("2022-01-05"), dt("2022-01-06")]

    def test_no_data_at_all_is_one_head_gap(self):
        assert ob.classify_gaps(self.EXPECTED, set())["head"] == self.EXPECTED


# ── Universe inserts ─────────────────────────────────────────────────────


class TestUniverseInserts:
    INTERVALS = {
        "KSU": [(dt("2021-01-01"), dt("2021-12-14"))],
        "DAY": [(dt("2021-09-20"), dt("2026-02-09"))],
        "AAPL": [(dt("2021-01-01"), None)],
        "SKYD": [(dt("2025-08-08"), None)],
        "ILMN": [(dt("2021-01-01"), dt("2024-06-24")), (dt("2026-09-21"), None)],
    }

    def test_missing_closed_tickers_become_inactive_rows(self):
        names = {"KSU": ob.NameInfo("Kansas City Southern", "NYSE")}
        inserts, skipped = ob.plan_universe_inserts(
            self.INTERVALS, {"AAPL"}, names, add_current=False
        )
        by = {i.ticker: i for i in inserts}
        assert set(by) == {"KSU", "DAY"}
        assert by["KSU"].active is False
        assert by["KSU"].name == "Kansas City Southern" and by["KSU"].exchange == "NYSE"
        assert by["DAY"].added_date == dt("2021-09-20") and by["DAY"].name is None

    def test_current_members_are_left_to_index_sync(self):
        _, skipped = ob.plan_universe_inserts(self.INTERVALS, {"AAPL"}, {}, False)
        assert skipped == ["ILMN", "SKYD"]  # open interval = current member

    def test_current_members_can_be_inserted_active(self):
        inserts, skipped = ob.plan_universe_inserts(self.INTERVALS, set(), {}, True)
        assert skipped == []
        assert {i.ticker: i.active for i in inserts} == {
            "AAPL": True, "DAY": False, "ILMN": True, "KSU": False, "SKYD": True,
        }  # fmt: skip
        assert {i.ticker: i.added_date for i in inserts}["ILMN"] == dt("2021-01-01")

    def test_existing_tickers_are_never_touched(self):
        inserts, skipped = ob.plan_universe_inserts(
            self.INTERVALS, set(self.INTERVALS), {}, True
        )
        assert inserts == [] and skipped == []

    def test_insert_statement_is_idempotent_and_marks_the_origin(self):
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        n = ob.insert_universe_rows(
            session,
            [ob.UniverseInsert("KSU", dt("2021-01-01"), False, "KSU Corp", "NYSE")],
        )
        assert n == 1
        stmt = session.execute.call_args.args[0]
        text = str(stmt.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT (ticker) DO NOTHING" in text
        params = stmt.compile(dialect=postgresql.dialect()).params
        assert params["added_by_m0"] == "sp500_history"
        assert params["is_active_m0"] is False
        assert params["company_name_m0"] == "KSU Corp"

    def test_empty_insert_does_not_touch_the_db(self):
        session = MagicMock()
        assert ob.insert_universe_rows(session, []) == 0
        session.execute.assert_not_called()


# ── Price plan ───────────────────────────────────────────────────────────


class TestPlanPrices:
    cal = ob.SessionCalendar(dt("2025-08-01"), dt("2025-08-29"))
    ds = dt("2025-08-04")
    last = dt("2025-08-29")

    def plan(self, intervals, stored_dates, **kw):
        return ob.plan_prices(
            intervals, stored_dates, self.cal, self.ds, self.last, **kw
        )

    def test_covered_ticker_is_not_planned(self):
        sessions = set(self.cal.between(dt("2025-08-04"), dt("2025-08-07")))
        plans = self.plan(
            {"A": [(dt("2025-01-01"), dt("2025-08-08"))]}, {"A": sessions}
        )
        assert plans == []

    def test_uncovered_ticker_gets_expected_missing_and_tasks(self):
        have = {dt("2025-08-04"), dt("2025-08-05")}
        (plan,) = self.plan({"A": [(dt("2025-01-01"), dt("2025-08-08"))]}, {"A": have})
        assert plan.expected == [dt(f"2025-08-0{n}") for n in (4, 5, 6, 7)]
        assert plan.missing == [dt("2025-08-06"), dt("2025-08-07")]
        (task,) = plan.tasks
        assert (task.symbol, task.start, task.end) == ("A", self.ds, dt("2025-08-07"))
        assert task.asof == self.ds  # first member day (member before the window)

    def test_ticker_without_any_rows_is_planned(self):
        (plan,) = self.plan({"A": [(dt("2025-08-05"), None)]}, {})
        assert plan.tasks[0].end == self.last
        assert plan.tasks[0].asof == dt("2025-08-05")

    def test_skipped_tickers_are_left_alone(self):
        assert (
            self.plan(
                {"PARA": [(dt("2025-01-01"), dt("2025-08-08"))]}, {}, skip={"PARA"}
            )
            == []
        )

    def test_last_asof_mode_is_passed_through(self):
        (plan,) = self.plan(
            {"A": [(dt("2025-01-01"), dt("2025-08-08"))]}, {}, asof_mode="last"
        )
        assert plan.tasks[0].asof == dt("2025-08-07")


class TestApplyFetch:
    def make_plan(self, stored_dates=()):
        expected = [dt("2022-01-03"), dt("2022-01-04"), dt("2022-01-05")]
        task = ob.FetchTask(
            "X", "X", dt("2021-12-01"), dt("2022-01-05"), dt("2021-12-01")
        )
        return ob.TickerPlan("X", expected, set(stored_dates), [task]), task

    def test_counts_rows_and_gaps(self):
        plan, task = self.make_plan()
        bars = {task: [bar("2021-12-30"), bar("2022-01-03"), bar("2022-01-04")]}
        ob.apply_fetch(plan, bars, {})
        assert len(plan.fetched) == 3 and len(plan.to_write) == 3
        assert plan.gaps["tail"] == [dt("2022-01-05")]
        assert plan.gaps["internal"] == [] and plan.gaps["head"] == []

    def test_second_run_writes_nothing(self):
        plan, task = self.make_plan(stored_dates={dt("2022-01-03"), dt("2022-01-04")})
        bars = {task: [bar("2022-01-03"), bar("2022-01-04")]}
        already = {dt("2022-01-03"): stored(10.0), dt("2022-01-04"): stored(10.0)}
        ob.apply_fetch(plan, bars, already)
        assert plan.to_write == []
        assert plan.gaps["tail"] == [dt("2022-01-05")]  # still reported

    def test_ticker_without_bars_is_a_head_gap(self):
        plan, task = self.make_plan()
        ob.apply_fetch(plan, {task: []}, {})
        assert plan.fetched == {} and plan.to_write == []
        assert len(plan.gaps["head"]) == 3


# ── PARA ─────────────────────────────────────────────────────────────────


def para_state(prices, ta_dates=(), universe=None):
    return ob.ParaState(
        universe=universe
        if universe is not None
        else {
            "company_name": "Banzai International",
            "is_active": True,
            "added_by": "politician_trades",
            "added_date": dt("2026-08-09"),
            "exchange": "NASDAQ",
            "sector": "Technology",
            "industry": None,
            "cusip": "X",
            "isin": None,
        },
        prices={dt(d): stored(c) for d, c in prices.items()},
        ta_dates={dt(d) for d in ta_dates},
    )


PARA_DS = dt("2025-08-01")


@pytest.fixture
def small_reference():
    with patch.object(ob, "PARA_REFERENCE_MIN_BARS", 3):
        yield


class TestParaFix:
    #: Paramount's real bars end 2025-08-06 (the merger closed on 2025-08-07)
    REFERENCE = [
        prow("2025-08-04", 11.0, "PARA"),
        prow("2025-08-05", 11.5, "PARA"),
        prow("2025-08-06", 12.0, "PARA"),
    ]

    def banzai(self):
        return para_state(
            {
                "2025-08-01": 89000.0,
                "2025-08-04": 89100.0,
                "2025-08-05": 90000.0,
                "2025-08-06": 91000.0,
                "2025-08-07": 91500.0,  # no Paramount bar on this day
                "2025-08-08": 92000.0,
                "2025-08-11": 3.0,  # Banzai after its reverse split
            },
            ta_dates=["2025-08-05", "2025-08-08", "2025-08-11", "2025-08-07"],
        )

    def test_rows_from_the_exit_date_on_are_always_deleted(self, small_reference):
        plan = ob.plan_para_fix(self.banzai(), self.REFERENCE, PARA_DS)
        assert plan.exit_dates == [dt("2025-08-08"), dt("2025-08-11")]
        assert dt("2025-08-08") in plan.delete_price_dates
        assert dt("2025-08-11") in plan.delete_price_dates

    def test_earlier_rows_without_a_paramount_bar_are_foreign(self, small_reference):
        plan = ob.plan_para_fix(self.banzai(), self.REFERENCE, PARA_DS)
        # 2025-08-01 and 2025-08-07 have no Paramount bar
        assert plan.foreign_dates == [dt("2025-08-01"), dt("2025-08-07")]
        assert plan.delete_price_dates == sorted(
            [dt("2025-08-01"), dt("2025-08-07"), dt("2025-08-08"), dt("2025-08-11")]
        )

    def test_rows_with_a_paramount_bar_are_overwritten_not_deleted(
        self, small_reference
    ):
        plan = ob.plan_para_fix(self.banzai(), self.REFERENCE, PARA_DS)
        assert dt("2025-08-05") not in plan.delete_price_dates
        assert [m[0] for m in plan.mismatched] == [
            dt("2025-08-04"), dt("2025-08-05"), dt("2025-08-06")
        ]  # fmt: skip
        assert {r["trade_date"] for r in plan.rows_to_write} == {
            dt("2025-08-04"), dt("2025-08-05"), dt("2025-08-06")
        }  # fmt: skip

    def test_technical_indicators_after_the_exit_or_without_bar_are_deleted(
        self, small_reference
    ):
        plan = ob.plan_para_fix(self.banzai(), self.REFERENCE, PARA_DS)
        assert plan.delete_ta_dates == [
            dt("2025-08-07"), dt("2025-08-08"), dt("2025-08-11")
        ]  # fmt: skip

    def test_price_discontinuities_are_reported(self, small_reference):
        plan = ob.plan_para_fix(self.banzai(), self.REFERENCE, PARA_DS)
        assert [j[0] for j in plan.jumps] == [dt("2025-08-11")]  # 92,000 -> 3

    def test_universe_row_of_banzai_is_updated(self, small_reference):
        plan = ob.plan_para_fix(self.banzai(), self.REFERENCE, PARA_DS)
        assert plan.universe_action == "update" and plan.ta_needed

    def test_missing_universe_row_is_inserted(self, small_reference):
        state = ob.ParaState(universe=None)
        assert (
            ob.plan_para_fix(state, self.REFERENCE, PARA_DS).universe_action == "insert"
        )

    def test_banzai_reference_is_rejected(self, small_reference):
        banzai_like = [prow(f"2025-08-0{n}", 89000.0, "PARA") for n in (4, 5, 6)]
        plan = ob.plan_para_fix(self.banzai(), banzai_like, PARA_DS)
        assert "not Paramount" in plan.reference_problem
        assert plan.foreign_dates == []  # nothing is judged by a bad reference

    def test_missing_reference_blocks_the_fix(self):
        plan = ob.plan_para_fix(self.banzai(), None, PARA_DS)
        assert "no Paramount reference" in plan.reference_problem
        session = MagicMock()
        with pytest.raises(ob.AbortError, match="cannot fix PARA"):
            ob.apply_para_fix(session, plan, "/tmp")
        session.execute.assert_not_called()

    def test_too_short_reference_is_rejected(self):
        plan = ob.plan_para_fix(
            self.banzai(), self.REFERENCE, PARA_DS
        )  # needs 800 bars
        assert "only 3 Paramount bars" in plan.reference_problem

    def test_check_reference_start(self, small_reference):
        late = {dt("2025-09-01") + timedelta(days=i): 10.0 for i in range(5)}
        assert "starts" in ob.check_reference(late, PARA_DS)
        ok = {PARA_DS + timedelta(days=i): 10.0 for i in range(5)}
        assert ob.check_reference(ok, PARA_DS) is None

    def test_fixed_state_has_nothing_to_do(self, small_reference):
        fixed = para_state(
            {"2025-08-04": 11.0, "2025-08-05": 11.5, "2025-08-06": 12.0},
            ta_dates=["2025-08-05"],
            universe={
                "company_name": ob.PARA_COMPANY_NAME, "is_active": False,
                "added_by": "x", "added_date": dt("2026-08-09"), "exchange": None,
                "sector": None, "industry": None, "cusip": None, "isin": None,
            },
        )  # fmt: skip
        plan = ob.plan_para_fix(fixed, self.REFERENCE, PARA_DS)
        assert not plan.changes_anything and not plan.ta_needed
        assert plan.universe_action is None and plan.rows_to_write == []

    def test_fixed_prices_without_indicators_still_need_ta(self, small_reference):
        fixed = para_state(
            {f"2025-08-{n:02d}": 11.0 for n in range(4, 20)},
            universe={
                "company_name": ob.PARA_COMPANY_NAME, "is_active": False,
                "added_by": "x", "added_date": dt("2026-08-09"), "exchange": None,
                "sector": None, "industry": None, "cusip": None, "isin": None,
            },
        )  # fmt: skip
        reference = [prow(f"2025-08-{n:02d}", 11.0, "PARA") for n in range(4, 7)]
        plan = ob.plan_para_fix(fixed, reference, PARA_DS)
        assert plan.ta_needed  # 16 price rows, no indicator row at all


class TestApplyParaFix:
    def plan(self):
        plan = ob.ParaFixPlan()
        plan.delete_price_dates = [dt("2025-08-08"), dt("2025-08-11")]
        plan.delete_ta_dates = [dt("2025-08-08")]
        plan.rows_to_write = [prow("2025-08-04", 11.0, "PARA")]
        plan.universe_action = "update"
        plan.reference = {dt("2025-08-04"): 11.0}
        return plan

    def test_order_backup_universe_deletes_then_upsert(self):
        calls = []
        session = MagicMock()
        session.execute.side_effect = lambda stmt, *a, **k: (
            calls.append(("execute", type(stmt).__name__, str(stmt)[:40]))
            or MagicMock(rowcount=2)
        )
        with (
            patch.object(
                ob,
                "_backup_rows",
                side_effect=lambda s, m, w, d, prefix: calls.append(("backup", prefix)),
            ),
            patch.object(
                ob,
                "upsert_price_rows",
                side_effect=lambda s, rows: calls.append(("upsert", len(rows))) or 1,
            ),
        ):
            counts = ob.apply_para_fix(session, self.plan(), "/ops")
        kinds = [c[0] if c[0] != "execute" else c[1] for c in calls]
        assert kinds == [
            "backup", "backup", "backup", "Update", "Delete", "Delete", "upsert"
        ]  # fmt: skip
        assert [c[1] for c in calls[:3]] == [
            "para_prices_daily", "para_technical_indicators", "para_universe"
        ]  # fmt: skip
        assert counts == {"prices_deleted": 2, "ta_deleted": 2, "prices_written": 1}

    def test_statements_only_touch_the_para_rows_from_the_plan(self):
        session = MagicMock()
        session.execute.return_value = MagicMock(rowcount=1)
        with (
            patch.object(ob, "_backup_rows"),
            patch.object(ob, "upsert_price_rows", return_value=1),
        ):
            ob.apply_para_fix(session, self.plan(), "/ops")
        update, delete_prices, delete_ta = (
            c.args[0] for c in session.execute.call_args_list
        )
        up = update.compile(dialect=postgresql.dialect()).params
        assert up["company_name"] == ob.PARA_COMPANY_NAME and up["is_active"] is False
        assert up["sector"] is None and up["cusip"] is None
        p = delete_prices.compile(dialect=postgresql.dialect()).params
        assert p["ticker_1"] == "PARA"
        assert p["trade_date_1"] == [dt("2025-08-08"), dt("2025-08-11")]
        t = delete_ta.compile(dialect=postgresql.dialect()).params
        assert t["ticker_1"] == "PARA" and t["trade_date_1"] == [dt("2025-08-08")]

    def test_nothing_to_do_writes_nothing(self):
        session = MagicMock()
        with patch.object(ob, "_backup_rows") as backup:
            counts = ob.apply_para_fix(session, ob.ParaFixPlan(), "/ops")
        assert counts == {"prices_deleted": 0, "ta_deleted": 0, "prices_written": 0}
        session.execute.assert_not_called()
        backup.assert_not_called()

    def test_missing_universe_row_is_inserted_inactive(self):
        plan = self.plan()
        plan.universe_action = "insert"
        plan.delete_price_dates, plan.delete_ta_dates = [], []
        session = MagicMock()
        with (
            patch.object(ob, "_backup_rows"),
            patch.object(ob, "insert_universe_rows") as insert,
            patch.object(ob, "upsert_price_rows", return_value=1),
        ):
            ob.apply_para_fix(session, plan, "/ops")
        (rows,) = insert.call_args.args[1:]
        assert rows[0].ticker == "PARA" and rows[0].active is False
        assert rows[0].name == ob.PARA_COMPANY_NAME

    def test_backup_failure_aborts_before_any_delete(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        session = MagicMock()
        scalars = session.execute.return_value.scalars.return_value
        scalars.all.return_value = []
        with pytest.raises(ob.AbortError, match="--backup-dir"):
            ob.apply_para_fix(session, self.plan(), str(blocker / "ops"))
        for call in session.execute.call_args_list:
            assert isinstance(call.args[0], Select)  # only reads happened


class TestOtherTables:
    def test_ticker_tables_cover_the_signal_tables_but_not_the_repaired_ones(self):
        names = {t.name for t in ob.ticker_tables()}
        assert {
            "feature_snapshots", "politician_trades", "short_interest",
            "insider_trades", "news_sentiment", "ark_holdings", "index_membership",
        } <= names  # fmt: skip
        assert not names & {"prices_daily", "technical_indicators", "universe"}

    def test_news_articles_are_matched_through_their_symbols_array(self):
        assert [t.name for t in ob.symbol_array_tables()] == ["news_articles"]
        session = MagicMock()
        session.execute.return_value.scalar_one.return_value = 1
        ob.count_ticker_rows(session, "PARA", dt("2025-08-08"))
        news = [
            c.args[0]
            for c in session.execute.call_args_list
            if "news_articles" in str(c.args[0])
        ]
        assert news and "= ANY (signals.news_articles.symbols)" in str(
            news[0].compile(dialect=postgresql.dialect())
        )

    def test_every_table_is_dated_by_a_date_column_where_one_exists(self):
        by = {t.name: ob.date_column(t) for t in ob.ticker_tables()}
        assert by["feature_snapshots"].name == "snapshot_date"
        assert by["politician_trades"].name == "disclosure_date"  # as the retention job
        assert by["short_interest"].name == "settlement_date"
        assert by["index_membership"].name == "valid_from"

    def test_counts_total_and_after_the_exit_date(self):
        session = MagicMock()
        session.execute.return_value.scalar_one.side_effect = lambda: 5
        rows = ob.count_ticker_rows(session, "PARA", dt("2025-08-08"))
        assert rows and all(total == 5 for _, total, _ in rows)
        assert "news_articles" in {name for name, _, _ in rows}  # symbols array
        statements = [c.args[0] for c in session.execute.call_args_list]
        assert all(isinstance(s, Select) for s in statements)  # counting only
        assert any(s.compile().params.get("ticker_1") == "PARA" for s in statements)

    def test_tables_without_rows_are_not_listed(self):
        session = MagicMock()
        session.execute.return_value.scalar_one.return_value = 0
        assert ob.count_ticker_rows(session, "PARA", dt("2025-08-08")) == []


# ── Company names ────────────────────────────────────────────────────────


class TestNames:
    @pytest.fixture
    def names(self):
        html = (FIXTURES / "wikipedia_sp500_changes.html").read_text(encoding="utf-8")
        tables = pd.read_html(io.StringIO(html))
        df = ob.WikipediaIndexHistoryParser()._find_changes_table(tables, "sp500")
        return ob.security_names_from_table(df)

    def test_removed_and_added_names_are_collected(self, names):
        assert names["TAP"] == "Molson Coors"
        assert names["ILMN"] == "Illumina, Inc."
        assert names["MBC"] == "MasterBrand"

    def test_the_newest_name_of_a_renamed_company_wins(self, names):
        assert names["DAY"] == "Dayforce"  # not "Ceridian"
        assert names["CDAY"] == "Ceridian"

    def test_a_name_is_also_filed_under_the_current_symbol(self, names):
        assert names["FBIN"] == "Fortune Brands Home & Security"  # row says FBHS

    def test_junk_cells_are_cleaned(self, names):
        assert names["ALLE"] == "Allegion"  # "ALLE |"

    def test_wikipedia_failure_falls_back_to_alpaca_assets(self):
        asset = ob.NameInfo("Fallback Corp", "NYSE")
        with (
            patch.object(
                ob, "wikipedia_security_names", side_effect=RuntimeError("down")
            ),
            patch.object(ob, "alpaca_asset", return_value=asset) as lookup,
        ):
            result = ob.collect_names(
                ["ABC"], HEADERS, "https://paper-api.alpaca.markets"
            )
        assert result["ABC"] == asset
        lookup.assert_called_once()

    def test_wikipedia_name_needs_no_alpaca_call(self):
        with (
            patch.object(
                ob, "wikipedia_security_names", return_value={"ABC": "Abc Inc"}
            ),
            patch.object(ob, "alpaca_asset") as lookup,
        ):
            result = ob.collect_names(["ABC"], HEADERS, "e")
        assert result["ABC"].name == "Abc Inc"
        lookup.assert_not_called()

    def test_unknown_asset_gives_no_name(self):
        with patch.object(ob.requests, "get", return_value=response(status=404)):
            assert (
                ob.alpaca_asset("ZZZ", HEADERS, "https://paper-api.alpaca.markets")
                is None
            )
        with patch.object(
            ob.requests, "get", return_value=response({"name": "N", "exchange": "NYSE"})
        ) as get:
            info = ob.alpaca_asset("ZZZ", HEADERS, "https://paper-api.alpaca.markets")
        assert info == ob.NameInfo("N", "NYSE")
        assert get.call_args.args[0] == "https://paper-api.alpaca.markets/v2/assets/ZZZ"


# ── Technical indicators ─────────────────────────────────────────────────


class TestTechnicalIndicators:
    def test_recompute_uses_the_computers_per_ticker_api(self):
        session = MagicMock()
        calls = []
        with patch(
            "trading_signals.derived.technical_indicators.TechnicalIndicatorsComputer"
        ) as computer:
            computer.return_value.recompute_ticker.side_effect = lambda t: (
                calls.append(t) or 10
            )
            stats = ob.recompute_ta(session, ["B", "A", "A"])
        assert calls == ["A", "B"]  # sorted, de-duplicated
        assert stats == {"tickers": 2, "ta_rows": 20, "errors": 0}
        assert session.begin_nested.call_count == 2  # one savepoint per ticker

    def test_a_failing_ticker_is_counted_not_fatal(self):
        session = MagicMock()
        with patch(
            "trading_signals.derived.technical_indicators.TechnicalIndicatorsComputer"
        ) as computer:
            computer.return_value.recompute_ticker.side_effect = [RuntimeError("x"), 7]
            stats = ob.recompute_ta(session, ["A", "B"])
        assert stats == {"tickers": 2, "ta_rows": 7, "errors": 1}

    def test_min_rows_matches_the_computer(self):
        from trading_signals.derived.technical_indicators import MIN_DAYS_RSI

        assert ob.MIN_TA_ROWS == MIN_DAYS_RSI


# ── Orchestration ────────────────────────────────────────────────────────


@pytest.fixture
def harness(tmp_path):
    state = SimpleNamespace(
        events=[],
        sessions=[],
        intervals={},
        universe=set(),
        stored_dates={},
        bars={},
        errors=[],
        para_state=ob.ParaState(),
        ta_pending=[],
        ctx=None,
    )

    ctx = ob.Context(
        ds=DS, last_session=LAST, feed="sip", headers=HEADERS,
        endpoint="https://paper-api.alpaca.markets",
    )  # fmt: skip
    state.ctx = ctx

    @contextmanager
    def fake_session(read_only):
        session = MagicMock()
        state.sessions.append(read_only)
        session.commit.side_effect = lambda: state.events.append("commit")
        yield session

    def fake_upsert(session, rows):
        state.events.append(("upsert", len(rows)))
        return len(rows)

    patches = [
        patch.object(ob, "make_context", return_value=ctx),
        patch.object(ob, "repair_session", fake_session),
        patch.object(ob, "announce_db_target"),
        patch.object(ob, "load_intervals", side_effect=lambda s: state.intervals),
        patch.object(ob, "load_universe", side_effect=lambda s: state.universe),
        patch.object(ob, "load_real_dates", side_effect=lambda *a: state.stored_dates),
        patch.object(ob, "load_stored_prices", return_value={}),
        patch.object(ob, "load_para_state", side_effect=lambda s: state.para_state),
        patch.object(ob, "ta_missing_tickers", side_effect=lambda s: state.ta_pending),
        patch.object(ob, "collect_names", return_value={}),
        patch.object(
            ob,
            "fetch_task_bars",
            side_effect=lambda tasks, h, f: (
                {t: state.bars.get(t.symbol, []) for t in tasks},
                list(state.errors),
            ),
        ),
        patch.object(
            ob,
            "insert_universe_rows",
            side_effect=lambda s, ins: (
                state.events.append(("universe", len(ins))) or len(ins)
            ),
        ),
        patch.object(
            ob,
            "apply_para_fix",
            side_effect=lambda s, p, d: (
                state.events.append("para")
                or {"prices_deleted": 0, "ta_deleted": 0, "prices_written": 0}
            ),
        ),
        patch.object(ob, "upsert_price_rows", side_effect=fake_upsert),
        patch.object(
            ob,
            "recompute_ta",
            side_effect=lambda s, t: (
                state.events.append(("ta", list(t)))
                or {"tickers": len(t), "ta_rows": 0, "errors": 0}
            ),
        ),
    ]
    for p in patches:
        p.start()
    yield state
    for p in patches:
        p.stop()


def removed_ksu(state):
    state.intervals = {
        "KSU": [(dt("2021-01-01"), dt("2021-12-14"))],
        **{f"C{i}": [(dt("2021-01-01"), dt("2021-06-01"))] for i in range(25)},
    }
    state.bars = {
        "KSU": [
            bar(d.isoformat()) for d in ob.SessionCalendar(DS, dt("2021-12-13")).days
        ]
    }


class TestRun:
    def test_dry_run_reads_fetches_and_writes_nothing(self, harness, capsys):
        removed_ksu(harness)
        assert ob.main([]) == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out and "INSERT 1 rows" in out and "KSU" in out
        assert "expected" in out and "fetched" in out
        assert harness.events == []  # no universe insert, no upsert, no TA, no commit
        assert harness.sessions == [True]  # only the read-only session

    def test_no_fetch_dry_run_needs_no_network(self, harness, capsys):
        removed_ksu(harness)
        with patch.object(
            ob, "fetch_task_bars", side_effect=AssertionError("network!")
        ):
            assert ob.main(["--no-fetch"]) == 0
        assert "DRY RUN" in capsys.readouterr().out

    def test_apply_runs_one_transaction_per_step_in_order(self, harness, capsys):
        removed_ksu(harness)
        assert ob.main(["--apply", "--fix-para"]) == 0
        steps = [e if isinstance(e, str) else e[0] for e in harness.events]
        assert steps == [
            "universe", "commit", "para", "commit", "upsert", "commit", "ta", "commit"
        ]  # fmt: skip
        assert harness.sessions == [True, False, False, False, False]
        out = capsys.readouterr().out
        assert (
            "STEP universe" in out and "STEP fix-para" in out and "STEP prices" in out
        )

    def test_apply_without_para_fix_skips_that_step(self, harness):
        removed_ksu(harness)
        assert ob.main(["--apply"]) == 0
        assert "para" not in harness.events
        ta = [e for e in harness.events if isinstance(e, tuple) and e[0] == "ta"]
        assert ta == [("ta", ["KSU"])]

    def test_apply_with_no_fetch_is_refused(self, harness, capsys):
        removed_ksu(harness)
        assert ob.main(["--apply", "--no-fetch"]) == 2
        assert "--no-fetch" in capsys.readouterr().out
        assert harness.events == []

    def test_failed_requests_block_apply_before_any_write(self, harness, capsys):
        removed_ksu(harness)
        harness.errors = ["['KSU'] 2021-10-01..2021-12-13 asof=2021-10-01: HTTP 500"]
        assert ob.main(["--apply"]) == 2
        assert "nothing written" in capsys.readouterr().out
        assert harness.events == []
        # ... but a dry run just reports them
        assert ob.main([]) == 0
        assert "FAILED REQUEST" in capsys.readouterr().out

    def test_feed_rejection_aborts(self, harness, capsys):
        removed_ksu(harness)
        with patch.object(ob, "fetch_task_bars", side_effect=ob.FeedError("no sip")):
            assert ob.main(["--apply"]) == 2
        assert "ABORT: no sip" in capsys.readouterr().out
        assert harness.events == []

    def test_second_run_after_everything_is_done_does_nothing(self, harness, capsys):
        removed_ksu(harness)
        harness.universe = set(harness.intervals)
        harness.stored_dates = {
            "KSU": set(ob.SessionCalendar(DS, dt("2021-12-13")).days)
        }
        assert ob.main(["--apply"]) == 0
        # the universe step runs with zero rows, nothing is upserted, no TA targets
        assert ("universe", 0) in harness.events
        assert ("upsert", 0) not in harness.events
        assert ("ta", []) in harness.events

    def test_tickers_option_restricts_the_plan(self, harness, capsys):
        harness.intervals = {
            "KSU": [(dt("2021-01-01"), dt("2021-12-14"))],
            "HBI": [(dt("2021-01-01"), dt("2021-12-20"))],
        }
        assert ob.main(["--tickers", "hbi"]) == 0
        out = capsys.readouterr().out
        assert "HBI" in out and "KSU" not in out.split("[2]")[0]

    def test_current_members_are_not_priced_without_a_universe_row(
        self, harness, capsys
    ):
        harness.intervals = {"SKYD": [(dt("2025-08-08"), None)]}
        assert ob.main([]) == 0
        out = capsys.readouterr().out
        assert "NOT inserted: SKYD" in out
        assert "0 tickers whose membership sessions are not covered" in out

    def test_current_members_with_the_flag_are_inserted_active_and_priced(
        self, harness, capsys
    ):
        harness.intervals = {"SKYD": [(dt("2025-08-08"), None)]}
        assert ob.main(["--add-current-members"]) == 0
        out = capsys.readouterr().out
        assert "ACTIVE" in out and "1 tickers whose membership sessions" in out

    def test_para_is_never_part_of_the_regular_price_plan(self, harness, capsys):
        harness.intervals = {"PARA": [(dt("2021-01-01"), dt("2025-08-08"))]}
        harness.universe = {"PARA"}
        assert ob.main([]) == 0
        out = capsys.readouterr().out
        assert "[3] PARA" in out and "check only" in out
        assert "0 tickers whose membership sessions are not covered" in out

    def test_para_fix_without_fetch_reports_but_never_writes(self, harness, capsys):
        harness.para_state = ob.ParaState(
            universe={
                "company_name": "Banzai", "is_active": True, "added_by": "x",
                "added_date": dt("2026-08-09"), "exchange": None, "sector": None,
                "industry": None, "cusip": None, "isin": None,
            },
            prices={dt("2025-08-08"): stored(89000.0)},
        )  # fmt: skip
        assert ob.main(["--fix-para", "--no-fetch"]) == 0
        out = capsys.readouterr().out
        assert "REFERENCE UNUSABLE" in out and "stored rows to delete: 1" in out
        assert harness.events == []

    def test_defaults_to_the_collectors_feed_and_warns_on_iex(self, harness, capsys):
        removed_ksu(harness)
        assert ob.main(["--no-fetch"]) == 0
        assert "WARNING: feed is not 'sip'" not in capsys.readouterr().out
        harness.ctx.feed = "iex"
        assert ob.main(["--no-fetch"]) == 0
        assert "WARNING: feed is not 'sip'" in capsys.readouterr().out
