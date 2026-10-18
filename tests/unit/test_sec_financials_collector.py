"""Tests for the SEC XBRL quarterly financials collector (no DB, no network)."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from trading_signals.collectors import sec_financials_collector as mod
from trading_signals.collectors.sec_financials_collector import (
    UPDATE_COLUMNS,
    SecFinancialsCollector,
)
from trading_signals.db.models.sec import FinancialsQuarterly

FACTS = json.loads(
    (
        Path(__file__).parent.parent / "fixtures" / "companyfacts_aapl_trimmed.json"
    ).read_text()
)
AAPL = "0000320193"
WINDOW = date(2024, 7, 1)


def _client(ciks, facts):
    client = MagicMock()
    client.get_cik.side_effect = lambda t: ciks.get(t)
    client.get_company_facts.side_effect = lambda cik: facts[cik]
    return client


def _fetch(collector, tickers):
    with (
        patch.object(mod, "data_start_date", return_value=WINDOW),
        patch.object(collector, "get_active_tickers", return_value=tickers),
    ):
        return collector.fetch(MagicMock())


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class TestFetch:
    def test_rows_of_every_ticker_with_cik_and_fetch_time(self):
        client = _client({"AAPL": AAPL}, {AAPL: FACTS})
        collector = SecFinancialsCollector(client=client)
        rows = _fetch(collector, ["AAPL"])
        assert len(rows) == 8
        assert {r["ticker"] for r in rows} == {"AAPL"}
        assert {r["cik"] for r in rows} == {AAPL}
        assert rows[-1]["period_end"] == date(2026, 6, 27)
        assert rows[-1]["revenue"] == 109_417_000_000
        assert rows[-1]["fetched_at"].tzinfo is not None
        client.load_cik_mapping.assert_called_once()
        assert (collector._attempts, collector._errors) == (1, 0)

    def test_share_classes_are_downloaded_once_and_stored_for_each_ticker(self):
        client = _client({"GOOG": AAPL, "GOOGL": AAPL}, {AAPL: FACTS})
        collector = SecFinancialsCollector(client=client)
        rows = _fetch(collector, ["GOOGL", "GOOG"])
        assert client.get_company_facts.call_count == 1
        assert {r["ticker"] for r in rows} == {"GOOG", "GOOGL"}
        assert len(rows) == 16
        goog = [r for r in rows if r["ticker"] == "GOOG"]
        googl = [r for r in rows if r["ticker"] == "GOOGL"]
        assert [r["revenue"] for r in goog] == [r["revenue"] for r in googl]

    def test_missing_cik_missing_facts_and_errors_do_not_stop_the_run(self):
        client = MagicMock()
        ciks = {"AAPL": AAPL, "NOFACTS": "0000000002", "BOOM": "0000000003"}
        client.get_cik.side_effect = lambda t: ciks.get(t)

        def facts(cik):
            if cik == "0000000003":
                raise RuntimeError("HTTP 503")
            return None if cik == "0000000002" else FACTS

        client.get_company_facts.side_effect = facts
        collector = SecFinancialsCollector(client=client)
        rows = _fetch(collector, ["AAPL", "BOOM", "NOFACTS", "SPY"])
        assert {r["ticker"] for r in rows} == {"AAPL"}
        # 404 counts as an answer; the 503 is an error; SPY has no CIK (no request)
        assert (collector._attempts, collector._errors) == (3, 1)

    def test_a_parser_error_is_recorded_and_the_run_goes_on(self):
        client = _client(
            {"AAPL": AAPL, "MSFT": "0000789019"}, {AAPL: FACTS, "0000789019": FACTS}
        )
        collector = SecFinancialsCollector(client=client)
        real = mod.build_quarterly_rows
        calls = []

        def flaky(facts, window):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("unexpected shape")
            return real(facts, window)

        with patch.object(mod, "build_quarterly_rows", side_effect=flaky):
            rows = _fetch(collector, ["AAPL", "MSFT"])
        assert {r["ticker"] for r in rows} == {"MSFT"}
        assert collector._errors == 1

    def test_companies_without_quarters_in_the_window_add_nothing(self):
        client = _client({"AAPL": AAPL}, {AAPL: {"facts": {"us-gaap": {}}}})
        collector = SecFinancialsCollector(client=client)
        assert _fetch(collector, ["AAPL"]) == []
        assert collector._errors == 0

    def test_only_tickers_restricts_the_run(self):
        client = _client(
            {"AAPL": AAPL, "MSFT": "0000789019"}, {AAPL: FACTS, "0000789019": FACTS}
        )
        collector = SecFinancialsCollector(client=client, only_tickers=["MSFT"])
        rows = _fetch(collector, ["AAPL", "MSFT"])
        client.get_company_facts.assert_called_once_with("0000789019")
        assert {r["ticker"] for r in rows} == {"MSFT"}

    def test_the_window_is_the_retention_window(self):
        client = _client({"AAPL": AAPL}, {AAPL: FACTS})
        collector = SecFinancialsCollector(client=client)
        with (
            patch.object(mod, "data_start_date", return_value=date(2026, 1, 1)),
            patch.object(collector, "get_active_tickers", return_value=["AAPL"]),
        ):
            rows = collector.fetch(MagicMock())
        assert [r["period_end"] for r in rows] == [date(2026, 3, 28), date(2026, 6, 27)]


class TestStore:
    def test_upsert_on_ticker_and_period_end(self):
        client = _client({"AAPL": AAPL}, {AAPL: FACTS})
        collector = SecFinancialsCollector(client=client)
        rows = _fetch(collector, ["AAPL"])
        session = MagicMock()
        session.execute.return_value.rowcount = len(rows)
        fetched, written = collector.store(session, rows)
        assert (fetched, written) == (8, 8)
        assert session.execute.call_count == 1
        sql = _sql(session.execute.call_args.args[0])
        assert sql.startswith("INSERT INTO signals.financials_quarterly")
        assert "ON CONFLICT (ticker, period_end) DO UPDATE SET" in sql
        session.flush.assert_called()

    def test_every_value_column_is_refreshed_on_conflict(self):
        columns = {c.name for c in FinancialsQuarterly.__table__.columns}
        assert set(UPDATE_COLUMNS) == columns - {"ticker", "period_end"}

    def test_nothing_to_store(self):
        session = MagicMock()
        assert SecFinancialsCollector(client=MagicMock()).store(session, []) == (0, 0)
        session.execute.assert_not_called()


def test_collector_identity():
    collector = SecFinancialsCollector(client=MagicMock())
    assert collector.name == "sec_financials" and collector.only_tickers is None
