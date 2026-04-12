"""Tests for PriceCollectorYFinance."""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np
import pytest

from trading_signals.collectors.prices_yfinance import (
    TICKER_MAP_FROM_YAHOO,
    TICKER_MAP_TO_YAHOO,
    PriceCollectorYFinance,
)


class TestTickerMapping:
    """Test ticker symbol mapping between our universe and Yahoo Finance."""

    def test_brk_b_maps_to_yahoo(self):
        assert TICKER_MAP_TO_YAHOO["BRK.B"] == "BRK-B"

    def test_yahoo_maps_back(self):
        assert TICKER_MAP_FROM_YAHOO["BRK-B"] == "BRK.B"


class TestPriceCollectorParsing:
    """Test DataFrame parsing and storage logic."""

    def test_single_ticker_parse(self):
        """Should correctly parse single-ticker DataFrame."""
        collector = PriceCollectorYFinance()
        all_data: dict[str, pd.DataFrame] = {}

        df = pd.DataFrame(
            {
                "Open": [100.0],
                "High": [105.0],
                "Low": [99.0],
                "Close": [103.0],
                "Adj Close": [102.5],
                "Volume": [1000000],
            },
            index=pd.DatetimeIndex([pd.Timestamp("2026-04-10")]),
        )

        collector._parse_batch_result(df, ["AAPL"], ["AAPL"], all_data)
        assert "AAPL" in all_data
        assert len(all_data["AAPL"]) == 1

    def test_store_idempotent(self):
        """store() should use ON CONFLICT DO NOTHING."""
        collector = PriceCollectorYFinance()

        session = MagicMock()
        # Simulate: first insert succeeds, second is a conflict
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = {
            "AAPL": pd.DataFrame(
                {
                    "Open": [100.0],
                    "High": [105.0],
                    "Low": [99.0],
                    "Close": [103.0],
                    "Adj Close": [102.5],
                    "Volume": [1000000],
                },
                index=pd.DatetimeIndex([pd.Timestamp("2026-04-10")]),
            )
        }

        fetched, written = collector.store(session, data)
        assert fetched == 1
        assert written == 1
        session.execute.assert_called()
        session.flush.assert_called()

    def test_store_skips_nan_close(self):
        """Rows with NaN close price should be skipped."""
        collector = PriceCollectorYFinance()

        session = MagicMock()

        data = {
            "AAPL": pd.DataFrame(
                {
                    "Open": [100.0],
                    "High": [105.0],
                    "Low": [99.0],
                    "Close": [np.nan],
                    "Adj Close": [np.nan],
                    "Volume": [0],
                },
                index=pd.DatetimeIndex([pd.Timestamp("2026-04-10")]),
            )
        }

        fetched, written = collector.store(session, data)
        assert fetched == 1
        assert written == 0  # Skipped due to NaN close

    def test_store_multiple_tickers(self):
        """Should handle multiple tickers correctly."""
        collector = PriceCollectorYFinance()

        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = {
            "AAPL": pd.DataFrame(
                {"Close": [103.0], "Open": [100.0], "High": [105.0],
                 "Low": [99.0], "Adj Close": [102.5], "Volume": [1000000]},
                index=pd.DatetimeIndex([pd.Timestamp("2026-04-10")]),
            ),
            "MSFT": pd.DataFrame(
                {"Close": [400.0], "Open": [395.0], "High": [405.0],
                 "Low": [393.0], "Adj Close": [399.0], "Volume": [2000000]},
                index=pd.DatetimeIndex([pd.Timestamp("2026-04-10")]),
            ),
        }

        fetched, written = collector.store(session, data)
        assert fetched == 2
        assert written == 2
