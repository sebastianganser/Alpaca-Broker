"""Tests for FRED Macro Regime Collector.

Tests the FredCollector with mocked FRED API responses.
Sprint 9.5b D1.
"""

from datetime import date, datetime
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
