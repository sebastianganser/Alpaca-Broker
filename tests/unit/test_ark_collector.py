"""Tests for ARKHoldingsCollector."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors.ark_holdings import (
    ARK_ETFS,
    NON_EQUITY_RE,
    ARKHoldingsCollector,
    _parse_date,
    _safe_numeric,
)


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class TestHelpers:
    """Test helper functions."""

    def test_parse_date_valid(self):
        assert _parse_date("2026-04-10").isoformat() == "2026-04-10"

    def test_parse_date_empty(self):
        assert _parse_date("") is None

    def test_parse_date_invalid(self):
        assert _parse_date("not-a-date") is None

    def test_safe_numeric_float(self):
        assert _safe_numeric(42.5) == 42.5

    def test_safe_numeric_int(self):
        assert _safe_numeric(1000) == 1000.0

    def test_safe_numeric_none(self):
        assert _safe_numeric(None) is None

    def test_safe_numeric_invalid(self):
        assert _safe_numeric("abc") is None


class TestNonEquityFilter:
    """Test the regex filter for non-equity positions."""

    def test_filters_cash(self):
        assert NON_EQUITY_RE.search("CASH")

    def test_filters_treasury(self):
        assert NON_EQUITY_RE.search("GOLDMAN FS TRSY OBLIG INST 468")

    def test_filters_empty_ticker(self):
        assert NON_EQUITY_RE.search("")

    def test_passes_normal_ticker(self):
        assert not NON_EQUITY_RE.search("TSLA")

    def test_passes_normal_company(self):
        assert not NON_EQUITY_RE.search("TESLA INC")


class TestARKETFList:
    """Test the ETF configuration."""

    def test_contains_core_etfs(self):
        assert "ARKK" in ARK_ETFS
        assert "ARKQ" in ARK_ETFS
        assert "ARKW" in ARK_ETFS

    def test_eight_etfs_tracked(self):
        assert len(ARK_ETFS) == 8

    def test_no_venture_fund(self):
        """ARKVX is not tradeable, should not be in the list."""
        assert "ARKVX" not in ARK_ETFS


class TestARKHoldingsCollector:
    """Test collector logic with mocked API."""

    def _load_fixture(self) -> dict:
        with open(FIXTURES_DIR / "ark_sample_response.json") as f:
            return json.load(f)

    @patch("trading_signals.collectors.ark_holdings._fetch_etf_holdings")
    def test_fetch_filters_cash_positions(self, mock_fetch):
        """Cash/treasury positions should be filtered out."""
        fixture = self._load_fixture()
        mock_fetch.return_value = fixture["holdings"]

        collector = ARKHoldingsCollector()
        session = MagicMock()

        data = collector.fetch(session)

        # Should have ARKK with 3 positions (TSLA, COIN, ROKU)
        # The cash position should be filtered out
        assert "ARKK" in data
        assert len(data["ARKK"]) == 3
        tickers = [h["ticker"] for h in data["ARKK"]]
        assert "TSLA" in tickers
        assert "" not in tickers  # Cash position filtered

    def test_store_writes_holdings(self):
        """store() should insert holdings into session."""
        collector = ARKHoldingsCollector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result
        # Mock get_active_tickers for universe expansion
        session.execute.return_value.scalars.return_value.all.return_value = []

        data = {
            "ARKK": [
                {
                    "ticker": "TSLA",
                    "company": "TESLA INC",
                    "date": "2026-04-10",
                    "cusip": "88160R101",
                    "shares": 1668128,
                    "market_value": 576538399.4,
                    "weight": 9.68,
                    "weight_rank": 1,
                    "share_price": 345.62,
                },
            ]
        }

        with patch.object(collector, "_expand_universe", return_value=[]):
            fetched, written = collector.store(session, data)

        assert fetched == 1
        assert written == 1
        session.flush.assert_called()
