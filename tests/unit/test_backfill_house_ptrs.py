"""Tests for scripts/backfill_house_ptrs.py (no DB, no network, fakes only)."""

import importlib.util
import io
import sys
import zipfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "house_ptr"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bf = _load("scripts/backfill_house_ptrs.py", "backfill_house_ptrs")

from trading_signals.collectors.house_disclosures import (  # noqa: E402
    HouseFiling,
    ptr_url,
)
from trading_signals.collectors.politician_trades_collector import (  # noqa: E402
    PoliticianTradesCollector,
)

EXTRACT_COLLECTOR = (
    "trading_signals.collectors.politician_trades_collector.extract_pdf_text"
)
ONBOARDER = "trading_signals.universe.onboarder.NewTickerOnboarder"
HEADER = (
    "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\r\n"
)

SINCE = date(2024, 1, 1)


def _filing(doc_id, fdate, ftype="P", year=None):
    return HouseFiling(
        "", "Last", "First", "", ftype, "CA01", year or fdate.year, fdate, doc_id
    )


def _index(year, rows):
    lines = [HEADER] + [
        f"\tL{doc}\tF\t\t{ftype}\tCA01\t{d.year}\t{d.month}/{d.day}/{d.year}\t{doc}\r\n"
        for doc, ftype, d in rows
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{year}FD.txt", b"\xef\xbb\xbf" + "".join(lines).encode())
    return buf.getvalue()


class FakeClient:
    def __init__(self, indexes):
        self.indexes = indexes
        self.pdf_calls = []

    def fetch_index(self, year):
        if year not in self.indexes:
            raise RuntimeError("no index")
        return self.indexes[year]

    def fetch_ptr_pdf(self, year, doc_id):
        self.pdf_calls.append((year, doc_id))
        return f"doc:{doc_id}".encode()


def _extract(pdf: bytes) -> str:
    doc_id = pdf.decode().split(":")[1]
    path = FIX / f"{doc_id}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


class FakeSessions:
    """Stand-in for ``repair_session``: records the mode of every session."""

    def __init__(self, stored=()):
        self.modes = []
        self.sessions = []
        self.stored = stored

    @contextmanager
    def __call__(self, read_only):
        s = MagicMock()
        s.execute.return_value.all.return_value = [(u,) for u in self.stored]
        s.execute.return_value.rowcount = 1
        self.modes.append(read_only)
        self.sessions.append(s)
        yield s


def _run(argv, client, sessions):
    with patch.object(bf, "repair_session", sessions), \
            patch.object(bf, "announce_db_target"), \
            patch.object(bf, "setup_logging"), \
            patch(EXTRACT_COLLECTOR, side_effect=_extract), \
            patch.object(bf, "extract_pdf_text", side_effect=_extract), \
            patch(ONBOARDER) as onboarder:
        code = bf.main(argv, client=client)
    return code, onboarder


class TestPlanning:
    def test_plan_year_classifies(self):
        stored_url = ptr_url(2024, "20000002")
        filings = [
            _filing("20000001", date(2024, 3, 1)),
            _filing("20000002", date(2024, 3, 2)),  # stored
            _filing("9000001", date(2024, 3, 3)),  # paper
            _filing("20000003", date(2024, 3, 4), ftype="A"),  # not a PTR
            _filing("20000004", date(2023, 12, 31), year=2024),  # before since
            _filing("20000001", date(2024, 3, 1)),  # duplicate
        ]
        plan = bf.plan_year(2024, filings, SINCE, {stored_url})
        assert (plan.ptr_total, plan.electronic, plan.paper) == (3, 2, 1)
        assert plan.already_stored == 1
        assert [f.doc_id for f in plan.to_fetch] == ["20000001"]
        assert "1 to fetch" in plan.line()

    def test_years_in_window(self):
        assert bf.years_in_window(date(2023, 6, 1), date(2025, 2, 1), None) == [
            2023, 2024, 2025,
        ]
        assert bf.years_in_window(date(2023, 6, 1), date(2025, 2, 1), 2024) == [2024]
        assert bf.years_in_window(date(2023, 6, 1), date(2025, 2, 1), 2020) == []

    def test_year_window_start(self):
        assert bf.year_window_start(date(2023, 6, 1), 2023) == date(2023, 6, 1)
        assert bf.year_window_start(date(2023, 6, 1), 2024) == date(2024, 1, 1)


class TestDryRun:
    def _client(self):
        return FakeClient({
            2024: _index(2024, [
                ("20022162", "P", date(2024, 3, 1)),
                ("20022087", "P", date(2024, 4, 1)),
                ("9116361", "P", date(2024, 5, 1)),
            ]),
        })

    def test_dry_run_is_read_only_and_downloads_no_pdf(self, capsys):
        client = self._client()
        sessions = FakeSessions()
        with patch.object(PoliticianTradesCollector, "store") as store:
            code, onboarder = _run(
                ["--since", "2024-01-01", "--year", "2024"], client, sessions
            )
        assert code == 0
        assert sessions.modes == [True]
        executed = [str(c[0][0]) for c in sessions.sessions[0].execute.call_args_list]
        assert any("default_transaction_read_only" in e for e in executed)
        assert client.pdf_calls == []
        store.assert_not_called()
        onboarder.assert_not_called()
        text = capsys.readouterr().out
        assert "2024: 3 PTR filings, 2 electronic, 1 paper (skipped)" in text
        assert "2 to fetch" in text
        assert "dry run done" in text

    def test_stored_urls_are_counted(self, capsys):
        sessions = FakeSessions(stored=[ptr_url(2024, "20022162")])
        _run(["--since", "2024-01-01", "--year", "2024"], self._client(), sessions)
        assert "1 already stored, 1 to fetch" in capsys.readouterr().out

    def test_sample_downloads_and_parses_n_filings(self, capsys):
        client = self._client()
        code, _ = _run(
            ["--since", "2024-01-01", "--year", "2024", "--sample", "5", "--seed", "1"],
            client,
            FakeSessions(),
        )
        assert code == 0
        assert sorted(d for _, d in client.pdf_calls) == ["20022087", "20022162"]
        text = capsys.readouterr().out
        assert "sampling 2 of 2" in text
        assert "sample rows: 7 total, 7 stock" in text
        assert "sample parse mismatches: 0" in text

    def test_index_error_gives_exit_code_1(self, capsys):
        client = FakeClient({})
        code, _ = _run(
            ["--since", "2024-01-01", "--year", "2024"], client, FakeSessions()
        )
        assert code == 1
        assert "ERROR fetching the index" in capsys.readouterr().out

    def test_since_before_the_retention_window_is_refused(self, capsys):
        client = self._client()
        code, _ = _run(["--since", "2015-01-01", "--apply"], client, FakeSessions())
        assert code == 2
        text = capsys.readouterr().out
        assert "REFUSED" in text and "retention" in text
        assert client.pdf_calls == []

    def test_empty_window_is_refused(self, capsys):
        code, _ = _run(
            ["--since", "2024-01-01", "--year", "1999"], FakeClient({}), FakeSessions()
        )
        assert code == 2
        assert "REFUSED" in capsys.readouterr().out


class TestApply:
    def test_apply_stores_per_year_and_commits_each_year(self, capsys):
        this_year = date.today().year
        d_prev = date(this_year - 1, 3, 1)
        d_now = date(this_year, 1, 1)
        client = FakeClient({
            this_year - 1: _index(this_year - 1, [("20022162", "P", d_prev)]),
            this_year: _index(this_year, [("20022087", "P", d_now)]),
        })
        sessions = FakeSessions()
        code, onboarder = _run(
            ["--since", f"{this_year - 1}-01-01", "--apply"], client, sessions
        )
        assert code == 0
        assert sessions.modes == [False, False]
        assert all(s.commit.called for s in sessions.sessions)
        assert [d for _, d in client.pdf_calls] == ["20022162", "20022087"]
        onboarder.assert_not_called()  # House tickers never reach the onboarder
        text = capsys.readouterr().out
        assert f"{this_year - 1}: stored" in text and f"{this_year}: stored" in text

    def test_apply_does_not_refetch_stored_filings(self):
        client = FakeClient({
            2024: _index(2024, [("20022162", "P", date(2024, 3, 1))]),
        })
        sessions = FakeSessions(stored=[ptr_url(2024, "20022162")])
        code, _ = _run(
            ["--since", "2024-01-01", "--year", "2024", "--apply"], client, sessions
        )
        assert code == 0
        assert client.pdf_calls == []

    def test_apply_reports_errors_with_exit_code_1(self, capsys):
        client = FakeClient({})
        code, _ = _run(
            ["--since", "2024-01-01", "--year", "2024", "--apply"],
            client,
            FakeSessions(),
        )
        assert code == 1
        assert "WARNING 1 errors" in capsys.readouterr().out
