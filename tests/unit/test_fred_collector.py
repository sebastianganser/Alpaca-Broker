"""Tests for FRED Macro Regime Collector.

Tests the FredCollector with mocked FRED API responses.
Sprint 9.5b D1.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors.fred_collector import (
    FRED_SERIES,
    FredCollector,
)
from trading_signals.db.models.macro_series import MacroSeries


class TestFredCollector:
    """Unit tests for FredCollector."""

    @patch("trading_signals.collectors.fred_collector.get_settings")
    def test_init_without_api_key_raises(self, mock_settings):
        """FredCollector should raise ValueError if FRED_API_KEY is empty."""
        mock_settings.return_value.FRED_API_KEY = ""
        with pytest.raises(ValueError, match="FRED_API_KEY not configured"):
            FredCollector()

    @patch("trading_signals.collectors.fred_collector.get_settings")
    def test_init_with_api_key(self, mock_settings):
        """FredCollector should initialize with a valid API key."""
        mock_settings.return_value.FRED_API_KEY = "test_key_12345"
        collector = FredCollector()
        assert collector._api_key == "test_key_12345"
        assert collector.name == "fred_collector"

    @patch("trading_signals.collectors.fred_collector.get_settings")
    def test_fetch_series_parses_response(self, mock_settings):
        """_fetch_series should parse FRED API JSON response."""
        mock_settings.return_value.FRED_API_KEY = "test_key"
        collector = FredCollector()

        # Mock the HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "observations": [
                {"date": "2024-01-02", "value": "4.25"},
                {"date": "2024-01-03", "value": "4.30"},
                {"date": "2024-01-04", "value": "."},  # Missing value
            ]
        }
        collector._session.get = MagicMock(return_value=mock_response)

        observations = collector._fetch_series(
            "DGS10", date(2024, 1, 1), date(2024, 1, 5)
        )

        assert len(observations) == 2  # "." is skipped
        assert observations[0]["series_id"] == "DGS10"
        assert observations[0]["obs_date"] == date(2024, 1, 2)
        assert observations[0]["value"] == 4.25
        assert observations[0]["source"] == "fred"
        assert observations[1]["value"] == 4.30

    @patch("trading_signals.collectors.fred_collector.get_settings")
    def test_fetch_series_handles_empty_response(self, mock_settings):
        """_fetch_series should return empty list for no observations."""
        mock_settings.return_value.FRED_API_KEY = "test_key"
        collector = FredCollector()

        mock_response = MagicMock()
        mock_response.json.return_value = {"observations": []}
        collector._session.get = MagicMock(return_value=mock_response)

        result = collector._fetch_series("VIXCLS", date(2024, 1, 1), date(2024, 1, 5))
        assert result == []

    def test_fred_series_contains_all_required(self):
        """FRED_SERIES should contain all 6 required macro series."""
        expected = {"DGS2", "DGS10", "BAMLH0A0HYM2", "VIXCLS", "DTWEXBGS", "T10YIE"}
        assert set(FRED_SERIES.keys()) == expected

    @patch("trading_signals.collectors.fred_collector.data_start_date")
    @patch("trading_signals.collectors.fred_collector.get_settings")
    def test_fetch_uses_retention_floor_and_latest(self, mock_settings, mock_start):
        """New series start at data_start_date(); known ones after latest."""
        mock_settings.return_value.FRED_API_KEY = "test_key"
        mock_start.return_value = date(2021, 10, 1)
        collector = FredCollector()
        collector._reset_run_state()
        session = MagicMock()
        session.execute.return_value.all.return_value = [
            ("DGS10", date(2026, 9, 30)),
            ("VIXCLS", date(2019, 1, 1)),  # older than retention floor
        ]
        calls = {}

        def _fake(series_id, start, end):
            calls[series_id] = start
            return []

        collector._fetch_series = _fake
        collector.fetch(session)

        assert calls["DGS10"] == date(2026, 10, 1)
        assert calls["VIXCLS"] == date(2021, 10, 1)
        assert calls["DGS2"] == date(2021, 10, 1)
        session.commit.assert_called()  # read txn released before HTTP
        assert collector._attempts == len(calls)
        assert collector._errors == 0

    @patch("trading_signals.collectors.fred_collector.get_settings")
    def test_fetch_error_does_not_log_api_key(self, mock_settings, caplog):
        """HTTP errors (URL contains api_key) must not leak the key (H4)."""
        import logging

        import requests

        secret = "SUPERSECRETKEY123"
        mock_settings.return_value.FRED_API_KEY = secret
        collector = FredCollector()
        collector._reset_run_state()
        session = MagicMock()
        session.execute.return_value.all.return_value = []

        resp = MagicMock(status_code=500)
        err = requests.exceptions.HTTPError(
            f"500 Server Error for url: https://x/?api_key={secret}", response=resp
        )
        collector._fetch_series = MagicMock(side_effect=err)

        with caplog.at_level(logging.DEBUG):
            result = collector.fetch(session)

        assert result == []
        assert collector._errors == len(FRED_SERIES)
        assert secret not in caplog.text
        assert "HTTP 500" in caplog.text

    @patch("trading_signals.collectors.fred_collector.get_settings")
    def test_store_single_multirow_insert(self, mock_settings):
        mock_settings.return_value.FRED_API_KEY = "test_key"
        collector = FredCollector()
        session = MagicMock()
        session.execute.return_value.rowcount = 2
        data = [
            {"series_id": "DGS10", "obs_date": date(2024, 1, d), "value": 4.0,
             "source": "fred", "as_of": date(2024, 1, 5)}
            for d in (2, 3)
        ]
        assert collector.store(session, data) == (2, 2)
        assert session.execute.call_count == 1


class TestMacroSeriesModel:
    """Unit tests for MacroSeries ORM model."""

    def test_repr(self):
        """MacroSeries __repr__ should be descriptive."""
        m = MacroSeries(
            series_id="DGS10",
            obs_date=date(2024, 1, 2),
            value=4.25,
            source="fred",
            as_of=date(2024, 1, 3),
        )
        r = repr(m)
        assert "DGS10" in r
        assert "2024-01-02" in r
        assert "4.25" in r

    def test_table_name(self):
        """MacroSeries should use the correct table name."""
        assert MacroSeries.__tablename__ == "macro_series"
        assert MacroSeries.__table_args__[-1] == {"schema": "signals"}
