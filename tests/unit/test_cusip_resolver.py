"""Tests for the CUSIP → ticker resolver (13F) and the ARK point-in-time universe."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trading_signals.collectors.cusip_resolver import (
    CusipResolver,
    normalize_cusip,
    normalize_figi_ticker,
    pick_figi_match,
)
from trading_signals.universe.manager import UniverseManager


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._payload


class FakeHttp:
    """Answers OpenFIGI mapping jobs from a dict cusip → record | None."""

    def __init__(self, table: dict[str, dict | None], fail_times: int = 0):
        self.table = table
        self.calls: list[list[dict]] = []
        self.fail_times = fail_times

    def post(self, url, json, headers, timeout):
        self.calls.append(json)
        if self.fail_times > 0:
            self.fail_times -= 1
            return FakeResponse({"error": "bad"}, status=400)
        out = []
        for job in json:
            rec = self.table.get(job["idValue"])
            if rec == "error":
                out.append({"error": "Invalid idValue"})
            elif rec is None:
                out.append({"warning": "No identifier found."})
            else:
                out.append({"data": [rec]})
        return FakeResponse(out)


def _resolver(table, api_key="", **kw) -> tuple[CusipResolver, FakeHttp]:
    http = FakeHttp(table, **kw)
    return CusipResolver(api_key=api_key, http=http, sleep=lambda s: None), http


class TestNormalisation:
    def test_cusip(self):
        assert normalize_cusip(" 037833100 ") == "037833100"
        assert normalize_cusip("g0750c108") == "G0750C108"
        assert normalize_cusip("12345") is None
        assert normalize_cusip(None) is None
        assert normalize_cusip("03783310-") is None

    def test_figi_ticker(self):
        assert normalize_figi_ticker("BRK/B") == "BRK.B"
        assert normalize_figi_ticker(" aapl ") == "AAPL"
        assert normalize_figi_ticker("") is None

    def test_pick_prefers_equity(self):
        data = [
            {"ticker": "XYZ 1 C", "marketSector": "Corp"},
            {"ticker": "XYZ", "marketSector": "Equity"},
        ]
        assert pick_figi_match(data)["ticker"] == "XYZ"
        assert pick_figi_match([{"ticker": None}]) is None
        assert pick_figi_match(None) is None


class TestOpenFigi:
    def test_batches_without_key_and_negative_results(self):
        table = {f"00000000{i}": {"ticker": f"T{i}", "marketSector": "Equity"}
                 for i in range(9)}
        cusips = sorted(table) + ["99999999X"]
        resolver, http = _resolver(table)
        rows = resolver._from_openfigi(cusips)
        # no key → 10 jobs per request
        assert [len(c) for c in http.calls] == [10]
        by = {r["cusip"]: r["ticker"] for r in rows}
        assert by["000000003"] == "T3"
        # negative result is cached as NULL ticker
        assert "99999999X" in by and by["99999999X"] is None
        assert all(c["idType"] == "ID_CUSIP" and c["exchCode"] == "US"
                   for c in http.calls[0])

    def test_key_uses_large_batches_and_header(self):
        table = {f"{i:09d}": {"ticker": "A", "marketSector": "Equity"} for i in range(150)}
        resolver, http = _resolver(table, api_key="k")
        resolver._from_openfigi(sorted(table))
        assert [len(c) for c in http.calls] == [100, 50]

    def test_error_entries_are_not_cached(self):
        resolver, _ = _resolver({"111111111": "error"})
        assert resolver._from_openfigi(["111111111"]) == []

    def test_aborts_after_consecutive_failures(self):
        table = {f"{i:09d}": {"ticker": "A"} for i in range(50)}
        resolver, http = _resolver(table, fail_times=100)
        assert resolver._from_openfigi(sorted(table)) == []
        # HTTP 400 is not retried → exactly MAX_CONSECUTIVE_FAILURES requests
        assert len(http.calls) == 3


class TestResolveFlow:
    def test_cache_then_local_then_api(self, monkeypatch):
        resolver, _ = _resolver({})
        saved: list[list[dict]] = []
        monkeypatch.setattr(
            resolver, "_from_cache",
            lambda s, c: ({"AAAAAAAA1": "AAA"}, [x for x in c if x != "AAAAAAAA1"]),
        )
        monkeypatch.setattr(
            resolver, "_from_local_sources",
            lambda s, c: {"BBBBBBBB2": ("BBB", "ark_holdings")},
        )
        monkeypatch.setattr(
            resolver, "_from_openfigi",
            lambda c: [{"cusip": x, "ticker": "CCC" if x == "CCCCCCCC3" else None,
                        "source": "openfigi"} for x in c],
        )
        monkeypatch.setattr(resolver, "_save", lambda s, rows: saved.append(rows))
        session = MagicMock()
        out = resolver.resolve(
            session, ["aaaaaaaa1", "BBBBBBBB2", "CCCCCCCC3", "DDDDDDDD4", None, "bad"]
        )
        assert out == {"AAAAAAAA1": "AAA", "BBBBBBBB2": "BBB", "CCCCCCCC3": "CCC"}
        assert saved[0] == [{"cusip": "BBBBBBBB2", "ticker": "BBB", "source": "ark_holdings"}]
        assert {r["cusip"] for r in saved[1]} == {"CCCCCCCC3", "DDDDDDDD4"}

    def test_no_api(self, monkeypatch):
        resolver, http = _resolver({"CCCCCCCC3": {"ticker": "C"}})
        monkeypatch.setattr(resolver, "_from_cache", lambda s, c: ({}, list(c)))
        monkeypatch.setattr(resolver, "_from_local_sources", lambda s, c: {})
        assert resolver.resolve(MagicMock(), ["CCCCCCCC3"], use_api=False) == {}
        assert http.calls == []


class TestCollectorIntegration:
    def test_fetch_rows_get_tickers(self):
        from trading_signals.collectors.form13f_collector import Form13FCollector

        resolver = MagicMock()
        resolver.resolve.return_value = {"037833100": "AAPL"}
        c = Form13FCollector(cusip_resolver=resolver, filers={"1": "x"})
        rows = [{"cusip": "037833100", "ticker": None}, {"cusip": "000000000"}]
        c._resolve_tickers(MagicMock(), rows)
        assert rows[0]["ticker"] == "AAPL"
        assert rows[1]["ticker"] is None
        assert c.filers == {"1": "x"}

    def test_resolver_failure_is_swallowed(self):
        from trading_signals.collectors.form13f_collector import Form13FCollector

        resolver = MagicMock()
        resolver.resolve.side_effect = RuntimeError("boom")
        c = Form13FCollector(cusip_resolver=resolver)
        rows = [{"cusip": "037833100"}]
        c._resolve_tickers(MagicMock(), rows)
        assert "ticker" not in rows[0]


class TestArkUniverse:
    @staticmethod
    def _session(index_tickers, ark_tickers, membership_rows=10):
        session = MagicMock()
        r_count = MagicMock()
        r_count.scalar_one.return_value = membership_rows
        r_idx = MagicMock()
        r_idx.all.return_value = [(t,) for t in index_tickers]
        r_ark = MagicMock()
        r_ark.all.return_value = [(t,) for t in ark_tickers]
        session.execute.side_effect = [r_count, r_idx, r_ark]
        return session

    def test_union_with_ark(self):
        s = self._session(["AAPL", "MSFT"], ["TSLA", "AAPL", "PATH"])
        out = UniverseManager(s).get_universe_as_of(date(2026, 6, 1))
        assert out == ["AAPL", "MSFT", "PATH", "TSLA"]

    def test_index_filter_skips_ark(self):
        s = self._session(["AAPL"], ["TSLA"])
        out = UniverseManager(s).get_universe_as_of(date(2026, 6, 1), index_name="sp500")
        assert out == ["AAPL"]
        assert s.execute.call_count == 2

    def test_ark_query_is_point_in_time(self):
        from sqlalchemy.dialects import postgresql

        s = MagicMock()
        s.execute.return_value.all.return_value = []
        UniverseManager(s)._ark_tickers_as_of(date(2026, 6, 30))
        sql = str(s.execute.call_args[0][0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        ))
        assert "snapshot_date <= '2026-06-30'" in sql
        assert "snapshot_date >= '2026-05-31'" in sql
        assert "JOIN signals.universe" in sql


def _load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "repair" / "form13f_resolve_tickers.py"
    spec = importlib.util.spec_from_file_location("form13f_resolve_tickers", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestRepairScript:
    def test_windows_cover_range(self):
        mod = _load_script()
        w = mod.backfill_windows(date(2021, 10, 1), date(2023, 1, 15), 365)
        assert w[0] == (date(2021, 10, 1), date(2022, 9, 30))
        assert w[-1][1] == date(2023, 1, 15)
        for (a, b), (c, _) in zip(w, w[1:], strict=False):
            assert (c - b).days == 1

    @pytest.mark.parametrize("argv,apply,backfill", [
        ([], False, False), (["--apply"], True, False),
        (["--apply", "--backfill"], True, True),
    ])
    def test_args(self, argv, apply, backfill):
        a = _load_script().parse_args(argv)
        assert a.apply is apply and a.backfill is backfill
