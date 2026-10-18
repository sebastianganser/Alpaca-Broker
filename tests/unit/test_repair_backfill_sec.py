"""Tests for scripts/repair/backfill_sec_filings.py and backfill_sec_financials.py.

No DB (the repair session and the collectors are mocked), no network.
"""

import importlib.util
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


filings = _load("scripts/repair/backfill_sec_filings.py", "backfill_sec_filings")
financials = _load(
    "scripts/repair/backfill_sec_financials.py", "backfill_sec_financials"
)


@contextmanager
def _read_only_session(session):
    yield session


def _filing(
    form="8-K", items=("2.02", "9.01"), cik="0000320193", day=date(2026, 7, 30)
):
    return {
        "filing": {
            "form": form,
            "items": list(items) if items else None,
            "cik": cik,
            "filing_date": day,
        },
        "accession_number": "a",
        "texts": [],
        "texts_done": False,
    }


class TestBackfillSecFilings:
    def test_defaults_are_a_dry_run_over_120_days(self):
        args = filings.parse_args([])
        assert (args.apply, args.days, args.tickers) == (False, 120, None)
        args = filings.parse_args(["--apply", "--days", "30", "--tickers", "AAPL"])
        assert (args.apply, args.days, args.tickers) == (True, 30, "AAPL")

    def test_summary_counts_forms_items_and_text_requests(self):
        entries = [
            _filing(),
            _filing(items=("5.02",)),
            _filing(items=("5.07", "9.01"), day=date(2026, 8, 1)),  # nothing to read
            _filing(form="144", items=None, cik="0000789019"),
            {
                "filing": None,
                "accession_number": "retry",
                "texts": [],
                "texts_done": False,
            },
        ]
        text = "\n".join(filings.summarize(entries))
        assert "new filings: 4 (companies: 2)" in text
        assert "8-K" in text and "144" in text
        assert "text requests an --apply run would make: 2" in text
        assert "filing dates: 2026-07-30 .. 2026-08-01" in text
        assert "5.02" in text and "9.01" in text

    def test_dry_run_reads_only_and_never_runs_or_writes(self, capsys):
        session = MagicMock()
        collector = MagicMock()
        collector.fetch.return_value = [_filing()]
        collector._attempts = 650
        with (
            patch.object(filings, "setup_logging"),
            patch.object(filings, "announce_db_target"),
            patch.object(
                filings,
                "repair_session",
                side_effect=lambda read_only: _read_only_session(session),
            ) as rs,
            patch(
                "trading_signals.collectors.sec_filings_collector.SecFilingsCollector",
                return_value=collector,
            ) as cls,
        ):
            assert filings.main([]) == 0
        rs.assert_called_once_with(read_only=True)
        assert cls.call_args.kwargs == {
            "lookback_days": 120,
            "fetch_texts": False,
            "only_tickers": None,
        }
        collector.run.assert_not_called()
        collector.fetch.assert_called_once_with(session)
        # the session stays read-only after the collector commits its read transaction
        assert "default_transaction_read_only" in str(session.execute.call_args.args[0])
        out = capsys.readouterr().out
        assert "mode: dry run" in out and "nothing written" in out

    def test_apply_runs_the_collector_with_the_given_window_and_tickers(self, capsys):
        collector = MagicMock()
        collector.run.return_value = MagicMock(
            id=7,
            collector_name="sec_filings",
            status="success",
            records_fetched=10,
            records_written=12,
            notes="ok",
        )
        with (
            patch.object(filings, "setup_logging"),
            patch.object(filings, "announce_db_target"),
            patch.object(filings, "repair_session") as rs,
            patch(
                "trading_signals.collectors.sec_filings_collector.SecFilingsCollector",
                return_value=collector,
            ) as cls,
        ):
            code = filings.main(["--apply", "--days", "30", "--tickers", "aapl, msft"])
        assert code == 0
        assert cls.call_args.kwargs == {
            "lookback_days": 30,
            "fetch_texts": True,
            "only_tickers": ["AAPL", "MSFT"],
        }
        rs.assert_not_called()  # the collector opens its own sessions
        collector.fetch.assert_not_called()
        assert "collection_log #7" in capsys.readouterr().out

    def test_a_failed_run_exits_with_1(self):
        collector = MagicMock()
        collector.run.return_value = MagicMock(
            id=8,
            collector_name="sec_filings",
            status="failed",
            records_fetched=0,
            records_written=0,
            notes="boom",
        )
        with (
            patch.object(filings, "setup_logging"),
            patch.object(filings, "announce_db_target"),
            patch(
                "trading_signals.collectors.sec_filings_collector.SecFilingsCollector",
                return_value=collector,
            ),
        ):
            assert filings.main(["--apply"]) == 1


def _quarter(ticker="AAPL", end=date(2026, 6, 27), **kw):
    row = {
        "ticker": ticker,
        "period_end": end,
        "fiscal_year": 2026,
        "fiscal_period": "Q3",
        "filed": date(2026, 7, 31),
        "revenue": 109.0,
        "net_income": 29.0,
        "free_cash_flow": 31.0,
    }
    row.update(kw)
    return row


class TestBackfillSecFinancials:
    def test_defaults(self):
        args = financials.parse_args([])
        assert (args.apply, args.tickers) == (False, None)

    def test_summary_reports_coverage_and_sample_tickers(self):
        rows = [
            _quarter(),
            _quarter(end=date(2026, 3, 28), revenue=None),
            _quarter(ticker="MSFT"),
        ]
        text = "\n".join(financials.summarize(rows))
        assert "rows: 3 for 2 tickers" in text
        assert "period_end: 2026-03-28 .. 2026-06-27" in text
        assert "revenue" in text and " 66.7 %" in text  # 2 of 3 rows
        assert "last quarters of AAPL:" in text and "last quarters of MSFT:" in text
        assert "last quarters of WMT:" not in text
        assert financials.summarize([]) == ["rows: 0 for 0 tickers"]

    def test_dry_run_reads_only_and_never_runs_or_writes(self, capsys):
        session = MagicMock()
        collector = MagicMock()
        collector.fetch.return_value = [_quarter()]
        collector._attempts = 651
        with (
            patch.object(financials, "setup_logging"),
            patch.object(financials, "announce_db_target"),
            patch.object(
                financials,
                "repair_session",
                side_effect=lambda read_only: _read_only_session(session),
            ) as rs,
            patch(
                "trading_signals.collectors.sec_financials_collector.SecFinancialsCollector",
                return_value=collector,
            ) as cls,
        ):
            assert financials.main(["--tickers", "AAPL"]) == 0
        rs.assert_called_once_with(read_only=True)
        assert cls.call_args.kwargs == {"only_tickers": ["AAPL"]}
        collector.run.assert_not_called()
        assert "default_transaction_read_only" in str(session.execute.call_args.args[0])
        assert "nothing written" in capsys.readouterr().out

    def test_apply_runs_the_collector(self, capsys):
        collector = MagicMock()
        collector.run.return_value = MagicMock(
            id=9,
            collector_name="sec_financials",
            status="partial",
            records_fetched=100,
            records_written=100,
            notes="2/650 requests failed",
        )
        with (
            patch.object(financials, "setup_logging"),
            patch.object(financials, "announce_db_target"),
            patch(
                "trading_signals.collectors.sec_financials_collector.SecFinancialsCollector",
                return_value=collector,
            ),
        ):
            assert financials.main(["--apply"]) == 0
        collector.fetch.assert_not_called()
        assert "collection_log #9" in capsys.readouterr().out
