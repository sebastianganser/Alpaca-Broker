"""Tests for OptionsIVCollector, ShortInterestCollector and EstimatesCollector.

Focus: session-date labelling + skip-if-stored (H3/M9), Yahoo symbol
mapping (M4), header auth / no key in URL (H4), run status (H5) and
multi-row inserts.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors.estimates_collector import EstimatesCollector
from trading_signals.collectors.options_iv_collector import OptionsIVCollector
from trading_signals.collectors.short_interest_collector import (
    ShortInterestCollector,
)

SESSION_DATE = date(2026, 10, 2)  # a Friday


def _session_with(stored: list[str], active: list[str]) -> MagicMock:
    """MagicMock session: 1st execute → stored tickers, 2nd → active tickers."""
    session = MagicMock()
    stored_res = MagicMock()
    stored_res.all.return_value = [(t,) for t in stored]
    active_res = MagicMock()
    active_res.all.return_value = [(t,) for t in active]
    session.execute.side_effect = [stored_res, active_res]
    return session


# ============================================================================
# Options IV
# ============================================================================


class TestOptionsIVCollector:
    @patch("trading_signals.collectors.options_iv_collector.time.sleep")
    @patch("trading_signals.collectors.options_iv_collector.market_calendar")
    def test_labels_with_last_completed_session(self, mock_cal, _sleep):
        mock_cal.last_completed_session.return_value = SESSION_DATE
        collector = OptionsIVCollector()
        collector._reset_run_state()
        seen = []

        def _fake(ticker, d):
            seen.append((ticker, d))
            return {"ticker": ticker, "snapshot_date": d, "atm_iv_30d": 0.3}

        collector._fetch_ticker_iv = _fake
        session = _session_with(stored=["AAPL"], active=["AAPL", "MSFT", "NVDA"])

        res = collector.fetch(session)

        # only the missing tickers, labelled with the session date
        assert seen == [("MSFT", SESSION_DATE), ("NVDA", SESSION_DATE)]
        assert {r["snapshot_date"] for r in res} == {SESSION_DATE}
        assert collector._attempts == 2 and collector._errors == 0
        session.commit.assert_called()

    @patch("trading_signals.collectors.options_iv_collector.market_calendar")
    def test_skips_when_session_already_stored(self, mock_cal):
        mock_cal.last_completed_session.return_value = SESSION_DATE
        collector = OptionsIVCollector()
        collector._fetch_ticker_iv = MagicMock()
        active = [f"T{i}" for i in range(10)]
        session = _session_with(stored=active[:9], active=active)

        assert collector.fetch(session) == []
        collector._fetch_ticker_iv.assert_not_called()

    @patch("trading_signals.collectors.options_iv_collector.time.sleep")
    @patch("trading_signals.collectors.options_iv_collector.market_calendar")
    def test_exceptions_count_as_errors(self, mock_cal, _sleep):
        mock_cal.last_completed_session.return_value = SESSION_DATE
        collector = OptionsIVCollector()
        collector._reset_run_state()
        collector._fetch_ticker_iv = MagicMock(side_effect=[RuntimeError("x"), None])
        session = _session_with(stored=[], active=["AAPL", "MSFT"])

        assert collector.fetch(session) == []
        assert collector._attempts == 2 and collector._errors == 1

    def test_fetch_ticker_uses_yahoo_symbol_and_original_ticker(self):
        import pandas as pd

        yticker = MagicMock()
        yticker.options = ("2026-10-30", "2026-11-27")
        chain = MagicMock()
        chain.calls = pd.DataFrame(
            {"strike": [100.0, 105.0], "impliedVolatility": [0.30, 0.28],
             "openInterest": [10, 5]}
        )
        chain.puts = pd.DataFrame(
            {"strike": [95.0, 100.0], "impliedVolatility": [0.35, 0.31],
             "openInterest": [7, 3]}
        )
        yticker.option_chain.return_value = chain
        yticker.fast_info = MagicMock(last_price=100.0)

        with patch("yfinance.Ticker", return_value=yticker) as mock_t:
            rec = OptionsIVCollector()._fetch_ticker_iv("BRK.B", SESSION_DATE)

        mock_t.assert_called_once_with("BRK-B")
        assert rec["ticker"] == "BRK.B"
        assert rec["snapshot_date"] == SESSION_DATE
        assert rec["atm_iv_30d"] == 0.30

    def test_store_single_multirow_insert(self):
        collector = OptionsIVCollector()
        session = MagicMock()
        session.execute.return_value.rowcount = 2
        recs = [
            {"ticker": t, "snapshot_date": SESSION_DATE, "atm_iv_30d": 0.2}
            for t in ("AAPL", "MSFT")
        ]
        assert collector.store(session, recs) == (2, 2)
        assert session.execute.call_count == 1


# ============================================================================
# Short interest
# ============================================================================


@pytest.fixture
def short_collector():
    with patch(
        "trading_signals.collectors.short_interest_collector.get_settings"
    ) as s:
        s.return_value.POLYGON_API_KEY = "SECRETKEY"
        yield ShortInterestCollector()


class TestShortInterestCollector:
    def test_api_key_sent_as_header_not_query(self, short_collector):
        resp = MagicMock(status_code=200, ok=True)
        resp.json.return_value = {
            "results": [{"short_volume": 50, "total_volume": 200}]
        }
        short_collector._session.get = MagicMock(return_value=resp)

        rec = short_collector._fetch_ticker_short_volume("AAPL", SESSION_DATE)

        _, kwargs = short_collector._session.get.call_args
        assert "apiKey" not in kwargs["params"]
        assert "SECRETKEY" not in str(kwargs["params"])
        assert short_collector._session.headers["Authorization"] == "Bearer SECRETKEY"
        assert rec["short_volume_ratio"] == 0.25
        assert rec["trade_date"] == SESSION_DATE

    @patch("trading_signals.collectors.short_interest_collector.time.sleep")
    @patch("trading_signals.collectors.short_interest_collector.market_calendar")
    def test_targets_last_session_and_skips_stored(
        self, mock_cal, mock_sleep, short_collector
    ):
        mock_cal.last_completed_session.return_value = SESSION_DATE
        short_collector._reset_run_state()
        short_collector._fetch_ticker_short_volume = MagicMock(
            side_effect=[{"ticker": "MSFT"}, Exception("boom")]
        )
        session = _session_with(stored=["AAPL"], active=["AAPL", "MSFT", "NVDA"])

        res = short_collector.fetch(session)

        calls = short_collector._fetch_ticker_short_volume.call_args_list
        assert [c.args for c in calls] == [
            ("MSFT", SESSION_DATE), ("NVDA", SESSION_DATE)
        ]
        assert res == [{"ticker": "MSFT"}]
        assert short_collector._attempts == 2 and short_collector._errors == 1
        assert mock_sleep.call_count == 1  # no sleep after the last request

    @patch("trading_signals.collectors.short_interest_collector.market_calendar")
    def test_weekend_rerun_returns_immediately(self, mock_cal, short_collector):
        mock_cal.last_completed_session.return_value = SESSION_DATE
        short_collector._fetch_ticker_short_volume = MagicMock()
        session = _session_with(stored=["AAPL", "MSFT"], active=["AAPL", "MSFT"])

        assert short_collector.fetch(session) == []
        short_collector._fetch_ticker_short_volume.assert_not_called()

    def test_error_description_has_no_url(self):
        import requests

        from trading_signals.collectors.short_interest_collector import _describe

        err = requests.exceptions.HTTPError(
            "403 for url: https://api.massive.com/x?ticker=A",
            response=MagicMock(status_code=403),
        )
        assert _describe(err) == "HTTPError (HTTP 403)"

    def test_store_single_multirow_insert(self, short_collector):
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        data = [{"ticker": "AAPL", "trade_date": SESSION_DATE, "short_volume": 1,
                 "total_volume": 2, "short_volume_ratio": 0.5, "source": "massive"}]
        assert short_collector.store(session, data) == (1, 1)
        assert session.execute.call_count == 1


# ============================================================================
# Estimates
# ============================================================================


class TestEstimatesCollector:
    @patch("trading_signals.collectors.estimates_collector.market_calendar")
    def test_fetch_only_missing_and_sets_as_of(self, mock_cal):
        mock_cal.last_completed_session.return_value = SESSION_DATE
        collector = EstimatesCollector()
        collector.client = MagicMock()
        collector.client.fetch_estimates.return_value = [
            {"ticker": "MSFT", "period": "0q", "eps_avg": 1.0}
        ]
        session = _session_with(stored=["AAPL"], active=["AAPL", "MSFT"])

        res = collector.fetch(session)

        args, kwargs = collector.client.fetch_estimates.call_args
        assert args[0] == ["MSFT"]
        assert kwargs["on_success"] == collector.record_success
        assert kwargs["on_error"] == collector.record_error
        assert res[0]["as_of"] == SESSION_DATE

    @patch("trading_signals.collectors.estimates_collector.market_calendar")
    def test_skip_when_stored(self, mock_cal):
        mock_cal.last_completed_session.return_value = SESSION_DATE
        collector = EstimatesCollector()
        collector.client = MagicMock()
        session = _session_with(stored=["AAPL", "MSFT"], active=["AAPL", "MSFT"])

        assert collector.fetch(session) == []
        collector.client.fetch_estimates.assert_not_called()

    def test_store_uses_record_as_of_and_one_statement(self):
        from sqlalchemy.dialects import postgresql

        collector = EstimatesCollector()
        session = MagicMock()
        session.execute.return_value.rowcount = 2
        data = [
            {"ticker": "AAPL", "period": p, "as_of": SESSION_DATE, "eps_avg": 1.0}
            for p in ("0q", "+1q")
        ]

        assert collector.store(session, data) == (2, 2)
        assert session.execute.call_count == 1
        compiled = session.execute.call_args[0][0].compile(
            dialect=postgresql.dialect()
        )
        as_ofs = {v for k, v in compiled.params.items() if k.startswith("as_of")}
        assert as_ofs == {SESSION_DATE}
        assert "DO NOTHING" in str(compiled)
