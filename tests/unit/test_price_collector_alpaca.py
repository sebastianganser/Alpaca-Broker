"""Tests for PriceCollectorAlpaca."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors.prices_alpaca import (
    BATCH_SIZE,
    PriceCollectorAlpaca,
    _parse_bar_timestamp,
    _fetch_bars_batch,
)


class TestParseBarTimestamp:
    """Test timestamp parsing from Alpaca bar format."""

    def test_valid_timestamp(self):
        assert _parse_bar_timestamp("2026-04-07T04:00:00Z") == date(2026, 4, 7)

    def test_valid_date_only(self):
        assert _parse_bar_timestamp("2026-04-07") == date(2026, 4, 7)

    def test_empty_string(self):
        assert _parse_bar_timestamp("") is None

    def test_none(self):
        assert _parse_bar_timestamp(None) is None

    def test_invalid(self):
        assert _parse_bar_timestamp("not-a-date") is None


class TestBatchSize:
    """Verify batch configuration."""

    def test_batch_size_is_100(self):
        assert BATCH_SIZE == 100


class TestPriceCollectorAlpaca:
    """Test collector logic with mocked API."""

    @patch("trading_signals.collectors.prices_alpaca._fetch_bars_batch")
    def test_fetch_batches_tickers(self, mock_fetch):
        """Verify fetch creates proper batches."""
        mock_fetch.return_value = {
            "AAPL": [{"c": 253.59, "h": 256.2, "l": 245.7, "o": 256.155,
                       "v": 1326699, "t": "2026-04-07T04:00:00Z"}],
        }

        collector = PriceCollectorAlpaca(lookback_days=5)
        session = MagicMock()

        # Mock universe query to return 3 tickers
        mock_rows = [(("AAPL",),), (("MSFT",),), (("TSLA",),)]
        session.execute.return_value.all.return_value = [
            ("AAPL",), ("MSFT",), ("TSLA",),
        ]

        data = collector.fetch(session)
        assert "AAPL" in data
        assert mock_fetch.called

    def test_store_writes_records(self):
        """store() should insert bars into session."""
        collector = PriceCollectorAlpaca()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = {
            "AAPL": [
                {
                    "c": 253.59, "h": 256.2, "l": 245.7, "o": 256.155,
                    "v": 1326699, "t": "2026-04-07T04:00:00Z",
                }
            ],
            "MSFT": [
                {
                    "c": 372.4, "h": 372.44, "l": 366.6, "o": 370.345,
                    "v": 617535, "t": "2026-04-07T04:00:00Z",
                }
            ],
        }

        fetched, written = collector.store(session, data)
        assert fetched == 2
        assert written == 2
        session.flush.assert_called()

    def test_store_skips_no_close(self):
        """Bars without close price should be skipped."""
        collector = PriceCollectorAlpaca()
        session = MagicMock()

        data = {
            "AAPL": [{"c": None, "h": 256.2, "l": 245.7, "o": 256.155,
                       "v": 1326699, "t": "2026-04-07T04:00:00Z"}],
        }

        fetched, written = collector.store(session, data)
        assert fetched == 1
        assert written == 0

    def test_store_skips_no_timestamp(self):
        """Bars without timestamp should be skipped."""
        collector = PriceCollectorAlpaca()
        session = MagicMock()

        data = {
            "AAPL": [{"c": 253.59, "h": 256.2, "l": 245.7, "o": 256.155,
                       "v": 1326699, "t": ""}],
        }

        fetched, written = collector.store(session, data)
        assert fetched == 1
        assert written == 0

    def test_adj_close_equals_close(self):
        """adj_close should equal close (adjustment=all means close IS adjusted)."""
        collector = PriceCollectorAlpaca()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = {
            "AAPL": [
                {"c": 253.59, "h": 256.2, "l": 245.7, "o": 256.155,
                 "v": 1326699, "t": "2026-04-07T04:00:00Z"}
            ],
        }

        collector.store(session, data)

        # Verify the insert statement was called with adj_close = close
        call_args = session.execute.call_args_list[0]
        stmt = call_args[0][0]
        # The compiled parameters should have adj_close = close
        compiled = stmt.compile()
        params = compiled.params
        assert params["adj_close"] == params["close"]
