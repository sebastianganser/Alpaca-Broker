"""Tests for ShortInterestPositionsCollector (FINRA short interest, Massive API).

No DB, no network: HTTP is a fake ``requests.Session.get``, DB reads are
patched out, ``store`` runs against a MagicMock session.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
import requests
from sqlalchemy.dialects import postgresql

from trading_signals.collectors import short_interest_positions_collector as mod
from trading_signals.collectors.short_interest_positions_collector import (
    ShortInterestPositionsCollector,
    clean_days_to_cover,
)

BASE = "https://api.massive.com"
URL = f"{BASE}/stocks/v1/short-interest"
START = date(2021, 7, 1)  # patched data_start_date()

D1, D2, D3, D4, D5, D6 = (
    date(2026, 6, 15),
    date(2026, 6, 30),
    date(2026, 7, 15),
    date(2026, 7, 31),
    date(2026, 8, 14),
    date(2026, 8, 31),
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _resp(payload=None, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = status < 400
    resp.json.return_value = payload if payload is not None else {"results": []}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status} for url: {URL}?apiKey=LEAK", response=resp
        )
    return resp


def _row(ticker: str, d: date, si: int = 1000, vol: int = 100, dtc=10.0) -> dict:
    return {
        "ticker": ticker,
        "settlement_date": d.isoformat(),
        "short_interest": si,
        "avg_daily_volume": vol,
        "days_to_cover": dtc,
    }


class FakeApi:
    """Routes ``session.get`` calls: reference listing vs. per-date bulk."""

    def __init__(self, reference_dates, by_date, errors=None):
        self.reference_dates = reference_dates
        self.by_date = by_date  # date -> list of rows | list of responses
        self.errors = errors or {}  # date -> status code (always failing)
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if params and params.get("ticker") == mod.REFERENCE_TICKER:
            return _resp({"results": [_row("AAPL", d) for d in self.reference_dates]})
        d = date.fromisoformat(params["settlement_date"])
        if d in self.errors:
            return _resp(status=self.errors[d])
        return _resp({"results": self.by_date.get(d, [])})

    @property
    def bulk_dates(self) -> list[date]:
        return [
            date.fromisoformat(p["settlement_date"])
            for _, p in self.calls
            if p and "settlement_date" in p
        ]


@pytest.fixture
def sleep():
    with patch.object(mod.time, "sleep") as s:
        yield s


@pytest.fixture(autouse=True)
def fixed_start():
    with patch.object(mod, "data_start_date", return_value=START):
        yield


@pytest.fixture
def collector():
    with patch.object(mod, "get_settings") as s:
        s.return_value.POLYGON_API_KEY = "SECRETKEY"
        c = ShortInterestPositionsCollector()
    c._reset_run_state()
    return c


def _run_fetch(collector, api, *, universe, stored, session=None):
    collector._session.get = api
    collector._read_universe = MagicMock(return_value=set(universe))
    collector._read_stored_dates = MagicMock(return_value=dict(stored))
    return collector.fetch(session or MagicMock())


# ── Construction / auth ──────────────────────────────────────────────────


class TestInit:
    def test_name(self, collector):
        assert collector.name == "short_interest_positions"

    def test_api_key_in_header_only(self, collector, sleep):
        api = FakeApi([D1], {})
        _run_fetch(collector, api, universe={"AAPL"}, stored={})
        assert collector._session.headers["Authorization"] == "Bearer SECRETKEY"
        for _, params in api.calls:
            assert "apiKey" not in params
            assert "SECRETKEY" not in str(params)

    def test_missing_key_raises(self):
        with patch.object(mod, "get_settings") as s:
            s.return_value.POLYGON_API_KEY = ""
            with pytest.raises(ValueError, match="POLYGON_API_KEY"):
                ShortInterestPositionsCollector()


# ── Date selection ───────────────────────────────────────────────────────


class TestSelectDates:
    select = staticmethod(ShortInterestPositionsCollector.select_dates)

    def test_missing_plus_two_most_recent_stored(self):
        stored = {D1: 700, D2: 700, D3: 700, D4: 700}
        got = self.select([D1, D2, D3, D4, D5, D6], stored)
        assert got == [D3, D4, D5, D6]  # D3/D4 = revisions, D5/D6 = new

    def test_nothing_missing_only_revisions(self):
        stored = {D1: 700, D2: 700, D3: 700}
        assert self.select([D1, D2, D3], stored) == [D2, D3]

    def test_empty_store_fetches_everything_ascending(self):
        assert self.select([D3, D1, D2, D2], {}) == [D1, D2, D3]

    def test_incomplete_stored_date_is_refetched(self):
        stored = {D1: 700, D2: 90, D3: 700, D4: 700, D5: 700}
        got = self.select([D1, D2, D3, D4, D5], stored)
        assert got == [D2, D4, D5]  # D2 incomplete + 2 most recent

    def test_slightly_smaller_date_is_not_incomplete(self):
        stored = {D1: 640, D2: 700, D3: 700, D4: 700}
        assert self.select([D1, D2, D3, D4], stored) == [D3, D4]

    def test_stored_date_not_listed_by_api_is_ignored(self):
        stored = {D1: 700, D2: 700, D6: 700}  # D6 unknown to the reference
        # D6 must not count as "most recent stored": revisions are D1 + D2
        assert self.select([D1, D2, D3], stored) == [D1, D2, D3]

    def test_refetch_all(self):
        stored = {D1: 700, D2: 700, D3: 700}
        assert self.select([D1, D2, D3, D4], stored, refetch_all=True) == [
            D1, D2, D3, D4
        ]


class TestRetentionCutoff:
    def test_reference_dates_before_window_are_dropped(self, collector, sleep):
        old = [date(2017, 12, 29), date(2021, 6, 15), date(2021, 6, 30)]
        new = [date(2021, 7, 15), D1]
        collector._session.get = FakeApi(old + new, {})

        assert collector._reference_dates(START) == new

    def test_old_dates_are_never_requested(self, collector, sleep):
        old = date(2021, 6, 30)
        api = FakeApi([old, D1], {D1: [_row("AAPL", D1)]})

        _run_fetch(collector, api, universe={"AAPL"}, stored={})

        assert api.bulk_dates == [D1]

    def test_reference_request_shape(self, collector, sleep):
        api = FakeApi([D1], {})
        _run_fetch(collector, api, universe={"AAPL"}, stored={})

        url, params = api.calls[0]
        assert url == URL
        assert params == {
            "ticker": "AAPL",
            "limit": 1000,
            "sort": "settlement_date.asc",
        }
        # bulk request: one date, all tickers
        assert api.calls[1][1] == {"settlement_date": D1.isoformat(), "limit": 50000}


# ── Row mapping ──────────────────────────────────────────────────────────


class TestMapping:
    def _map(self, collector, rows, universe=frozenset({"AAPL", "MSFT"}), d=D1):
        return collector._map_rows(rows, d, set(universe))

    def test_filters_to_universe(self, collector):
        rows = [_row("AAPL", D1), _row("ZZZZ", D1), _row("msft", D1), _row("OTCX", D1)]
        out = self._map(collector, rows)
        assert [r["ticker"] for r in out] == ["AAPL", "MSFT"]

    def test_maps_fields(self, collector):
        (r,) = self._map(collector, [_row("AAPL", D1, si=1234, vol=500, dtc=2.4681)])
        assert r["ticker"] == "AAPL"
        assert r["settlement_date"] == D1
        assert r["short_interest"] == 1234
        assert r["avg_daily_volume"] == 500
        assert r["days_to_cover"] == 2.4681
        assert r["source"] == "massive"
        assert r["fetched_at"] is not None

    def test_sentinel_999_99_becomes_null(self, collector):
        (r,) = self._map(collector, [_row("AAPL", D1, vol=100, dtc=999.99)])
        assert r["days_to_cover"] is None
        assert r["short_interest"] == 1000  # the rest of the row survives

    def test_zero_avg_volume_gives_null_days_to_cover(self, collector):
        (r,) = self._map(collector, [_row("AAPL", D1, vol=0, dtc=999.99)])
        assert r["avg_daily_volume"] == 0
        assert r["days_to_cover"] is None
        # even a non-sentinel value is meaningless without volume
        (r2,) = self._map(collector, [_row("AAPL", D1, vol=0, dtc=5.0)])
        assert r2["days_to_cover"] is None

    def test_clean_days_to_cover_edge_cases(self):
        assert clean_days_to_cover(3.14159, 10) == 3.1416
        assert clean_days_to_cover("2.5", 10) == 2.5
        assert clean_days_to_cover(998.99, 10) == 998.99
        assert clean_days_to_cover(999.0, 10) is None
        assert clean_days_to_cover(None, 10) is None
        assert clean_days_to_cover(-1.0, 10) is None
        assert clean_days_to_cover("n/a", 10) is None

    def test_missing_short_interest_row_is_skipped(self, collector):
        row = _row("AAPL", D1)
        row["short_interest"] = None
        assert self._map(collector, [row]) == []

    def test_zero_short_interest_is_kept(self, collector):
        (r,) = self._map(collector, [_row("AAPL", D1, si=0, vol=10, dtc=0.0)])
        assert r["short_interest"] == 0
        assert r["days_to_cover"] == 0.0

    def test_row_with_other_settlement_date_is_skipped(self, collector):
        assert self._map(collector, [_row("AAPL", D2)], d=D1) == []

    def test_garbage_rows_do_not_raise(self, collector):
        rows = [None, "x", {}, {"ticker": 5}, {"ticker": "AAPL"}]
        assert self._map(collector, rows) == []


# ── Fetch flow ───────────────────────────────────────────────────────────


class TestFetch:
    def test_fetches_missing_and_recent_dates_and_filters_universe(
        self, collector, sleep
    ):
        by_date = {
            d: [_row("AAPL", d), _row("MSFT", d), _row("OTCX", d)] for d in (D1, D2, D3)
        }
        api = FakeApi([D1, D2, D3], by_date)

        rows = _run_fetch(
            collector,
            api,
            universe={"AAPL", "MSFT"},
            stored={D1: 2},  # D1 stored; D2/D3 missing; D1 = only stored date
        )

        assert api.bulk_dates == [D1, D2, D3]  # D1 re-fetched as revision
        assert len(rows) == 6
        assert {r["ticker"] for r in rows} == {"AAPL", "MSFT"}
        assert collector._attempts == 4 and collector._errors == 0

    def test_only_revisions_when_up_to_date(self, collector, sleep):
        api = FakeApi([D1, D2, D3], {d: [_row("AAPL", d)] for d in (D1, D2, D3)})

        rows = _run_fetch(
            collector, api, universe={"AAPL"}, stored={D1: 1, D2: 1, D3: 1}
        )

        assert api.bulk_dates == [D2, D3]
        assert len(rows) == 2

    def test_failed_date_does_not_stop_the_others(self, collector, sleep):
        api = FakeApi(
            [D1, D2, D3],
            {d: [_row("AAPL", d)] for d in (D1, D2, D3)},
            errors={D2: 404},
        )
        collector.record_error = MagicMock(wraps=collector.record_error)
        collector.record_success = MagicMock(wraps=collector.record_success)

        rows = _run_fetch(collector, api, universe={"AAPL"}, stored={})

        assert api.bulk_dates == [D1, D2, D3]
        assert sorted(r["settlement_date"] for r in rows) == [D1, D3]
        collector.record_error.assert_called_once_with()
        assert collector.record_success.call_count == 3  # reference + D1 + D3
        assert collector._attempts == 4 and collector._errors == 1

    def test_failed_date_status_is_partial_not_failed(self, collector, sleep):
        # 2 errors out of 12 attempts (17 % > 10 %) -> partial, not failed
        dates = [date(2026, 1, 1 + i) for i in range(11)]
        api = FakeApi(
            dates,
            {d: [_row("AAPL", d)] for d in dates},
            errors={dates[0]: 404, dates[1]: 404},
        )
        rows = _run_fetch(collector, api, universe={"AAPL"}, stored={})

        status, note = collector._compute_status(len(rows))
        assert status == "partial"
        assert "2/12 requests failed" in note

    def test_reference_failure_aborts_without_bulk_requests(self, collector, sleep):
        collector._session.get = MagicMock(return_value=_resp(status=403))
        collector._read_universe = MagicMock(return_value={"AAPL"})
        collector._read_stored_dates = MagicMock(return_value={})

        assert collector.fetch(MagicMock()) == []

        assert collector._session.get.call_count == 1
        assert collector._attempts == 1 and collector._errors == 1
        assert collector._compute_status(0)[0] == "failed"

    def test_empty_universe_makes_no_requests(self, collector, sleep):
        api = FakeApi([D1], {})
        assert _run_fetch(collector, api, universe=set(), stored={}) == []
        assert api.calls == []

    def test_refetch_all_fetches_every_date(self, collector, sleep):
        collector.refetch_all = True
        api = FakeApi([D1, D2, D3], {d: [_row("AAPL", d)] for d in (D1, D2, D3)})

        _run_fetch(collector, api, universe={"AAPL"}, stored={D1: 1, D2: 1, D3: 1})

        assert api.bulk_dates == [D1, D2, D3]

    def test_read_transaction_ended_before_http(self, collector, sleep):
        session = MagicMock()
        api = FakeApi([D1], {D1: [_row("AAPL", D1)]})
        seen = []

        def spy(url, params=None, timeout=None):
            seen.append(session.commit.called)
            return api(url, params, timeout)

        collector._session.get = spy
        collector._read_universe = MagicMock(return_value={"AAPL"})
        collector._read_stored_dates = MagicMock(return_value={})

        collector.fetch(session)

        assert seen and all(seen)

    def test_pause_between_requests_but_not_before_the_first(self, collector, sleep):
        api = FakeApi([D1, D2], {})
        _run_fetch(collector, api, universe={"AAPL"}, stored={})

        # reference + 2 bulk requests -> 2 pauses of 12 s
        assert [c.args for c in sleep.call_args_list] == [(12,), (12,)]


# ── HTTP behaviour ───────────────────────────────────────────────────────


class TestHttp:
    def test_retries_429_then_succeeds(self, collector, sleep):
        collector._session.get = MagicMock(
            side_effect=[_resp(status=429), _resp({"results": [_row("AAPL", D1)]})]
        )

        rows = collector._fetch_date_rows(D1)

        assert len(rows) == 1
        assert collector._session.get.call_count == 2
        assert sleep.call_count == 1  # paced retry

    def test_retries_5xx_and_gives_up_after_max_attempts(self, collector, sleep):
        collector._session.get = MagicMock(return_value=_resp(status=503))

        with pytest.raises(requests.HTTPError):
            collector._fetch_date_rows(D1)

        assert collector._session.get.call_count == mod.MAX_ATTEMPTS

    def test_retries_network_errors(self, collector, sleep):
        collector._session.get = MagicMock(
            side_effect=[
                requests.ConnectionError("boom"),
                _resp({"results": [_row("AAPL", D1)]}),
            ]
        )
        assert len(collector._fetch_date_rows(D1)) == 1

    def test_client_error_is_not_retried(self, collector, sleep):
        collector._session.get = MagicMock(return_value=_resp(status=404))

        with pytest.raises(requests.HTTPError):
            collector._fetch_date_rows(D1)

        assert collector._session.get.call_count == 1

    def test_next_url_is_followed(self, collector, sleep):
        page2 = f"{BASE}/stocks/v1/short-interest?cursor=abc"
        collector._session.get = MagicMock(
            side_effect=[
                _resp({"results": [_row("AAPL", D1)], "next_url": page2}),
                _resp({"results": [_row("MSFT", D1)]}),
            ]
        )

        rows = collector._fetch_date_rows(D1)

        assert [r["ticker"] for r in rows] == ["AAPL", "MSFT"]
        first, second = collector._session.get.call_args_list
        assert first.args[0] == URL
        assert first.kwargs["params"]["settlement_date"] == D1.isoformat()
        assert second.args[0] == page2
        assert second.kwargs["params"] is None  # cursor lives in the URL
        assert "apiKey" not in page2  # key stays in the Bearer header

    def test_next_url_to_foreign_host_is_refused(self, collector, sleep):
        collector._session.get = MagicMock(
            return_value=_resp(
                {"results": [_row("AAPL", D1)], "next_url": "https://evil.example/x"}
            )
        )

        with pytest.raises(mod._ApiError):
            collector._fetch_date_rows(D1)

        assert collector._session.get.call_count == 1  # token never sent there

    def test_paginated_date_with_bad_next_url_counts_as_error(self, collector, sleep):
        collector._session.get = MagicMock(
            side_effect=[
                _resp({"results": [_row("AAPL", D1)]}),  # reference
                _resp({"results": [_row("AAPL", D1)], "next_url": "https://evil/x"}),
            ]
        )
        collector._read_universe = MagicMock(return_value={"AAPL"})
        collector._read_stored_dates = MagicMock(return_value={})

        assert collector.fetch(MagicMock()) == []
        assert collector._errors == 1

    def test_error_description_has_no_url(self):
        err = requests.HTTPError(
            f"403 for url: {URL}?ticker=A", response=MagicMock(status_code=403)
        )
        assert mod._describe(err) == "HTTPError (HTTP 403)"
        assert mod._describe(requests.ConnectionError(URL)) == "ConnectionError"

    def test_payload_shapes(self):
        assert mod._extract_rows({"results": [{"a": 1}, "x"]}) == [{"a": 1}]
        assert mod._extract_rows([{"a": 1}]) == [{"a": 1}]
        assert mod._extract_rows({"status": "OK"}) == []
        assert mod._extract_rows(None) == []


# ── Store ────────────────────────────────────────────────────────────────


class TestStore:
    def _rows(self):
        return [
            {
                "ticker": t,
                "settlement_date": D1,
                "short_interest": 1000,
                "avg_daily_volume": 100,
                "days_to_cover": 10.0,
                "source": "massive",
                "fetched_at": datetime(2026, 10, 18, 12, 0),
            }
            for t in ("AAPL", "MSFT")
        ]

    def test_store_builds_an_upsert(self, collector):
        session = MagicMock()
        session.execute.return_value.rowcount = 2

        assert collector.store(session, self._rows()) == (2, 2)

        assert session.execute.call_count == 1
        sql = str(session.execute.call_args[0][0].compile(dialect=postgresql.dialect()))
        assert "INSERT INTO signals.short_interest" in sql
        assert "ON CONFLICT (ticker, settlement_date, source) DO UPDATE" in sql
        for col in (
            "short_interest",
            "avg_daily_volume",
            "days_to_cover",
            "fetched_at",
        ):
            assert f"{col} = excluded.{col}" in sql
        # key columns are never rewritten
        assert "ticker = excluded.ticker" not in sql
        assert "settlement_date = excluded.settlement_date" not in sql

    def test_store_empty_does_nothing(self, collector):
        session = MagicMock()
        assert collector.store(session, []) == (0, 0)
        session.execute.assert_not_called()

    def test_store_dedupes_same_key_within_batch(self, collector):
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        a = dict(self._rows()[0], short_interest=1)
        b = dict(a, short_interest=2)  # same key, later value wins

        fetched, _ = collector.store(session, [a, b])

        assert fetched == 2
        params = session.execute.call_args[0][0].compile(
            dialect=postgresql.dialect()
        ).params
        assert sum(1 for k in params if k.startswith("ticker")) == 1
        assert params["short_interest_m0"] == 2
