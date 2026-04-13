"""Tests for YFinanceClient – rate-limiting, batching, and graceful error handling."""

import time
from datetime import date
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from trading_signals.collectors.yfinance_client import (
    YFinanceClient,
    _clean_numeric,
    FUNDAMENTALS_KEYS,
)


# ============================================================================
# Tests: _clean_numeric helper
# ============================================================================


class TestCleanNumeric:
    """Test the _clean_numeric helper function."""

    def test_valid_float(self):
        assert _clean_numeric(25.5) == 25.5

    def test_valid_int(self):
        assert _clean_numeric(100) == 100.0

    def test_valid_string_number(self):
        assert _clean_numeric("42.5") == 42.5

    def test_none_returns_none(self):
        assert _clean_numeric(None) is None

    def test_nan_returns_none(self):
        assert _clean_numeric(float("nan")) is None

    def test_inf_returns_none(self):
        assert _clean_numeric(float("inf")) is None

    def test_neg_inf_returns_none(self):
        assert _clean_numeric(float("-inf")) is None

    def test_invalid_string_returns_none(self):
        assert _clean_numeric("N/A") is None

    def test_empty_string_returns_none(self):
        assert _clean_numeric("") is None

    def test_zero(self):
        assert _clean_numeric(0) == 0.0

    def test_negative(self):
        assert _clean_numeric(-5.5) == -5.5


# ============================================================================
# Tests: YFinanceClient initialization
# ============================================================================


class TestYFinanceClientInit:
    """Test YFinanceClient initialization and configuration."""

    def test_default_params(self):
        client = YFinanceClient()
        assert client.batch_size == 50
        assert client.delay_between_tickers == 0.5
        assert client.delay_between_batches == 3.0

    def test_custom_params(self):
        client = YFinanceClient(
            batch_size=10,
            delay_between_tickers=0.1,
            delay_between_batches=1.0,
        )
        assert client.batch_size == 10
        assert client.delay_between_tickers == 0.1
        assert client.delay_between_batches == 1.0


# ============================================================================
# Tests: Batch iteration and rate-limiting
# ============================================================================


class TestBatchIteration:
    """Test the batch iteration with rate-limiting logic."""

    def test_single_batch(self):
        """All tickers fit in one batch."""
        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        tickers = ["AAPL", "MSFT", "GOOGL"]
        results = client._iterate_with_rate_limit(
            tickers,
            lambda t: {"ticker": t},
            "test",
        )
        assert len(results) == 3

    def test_multiple_batches(self):
        """Tickers split across multiple batches."""
        client = YFinanceClient(
            batch_size=2, delay_between_tickers=0, delay_between_batches=0
        )
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]
        results = client._iterate_with_rate_limit(
            tickers,
            lambda t: {"ticker": t},
            "test",
        )
        assert len(results) == 5

    def test_graceful_error_handling(self):
        """Individual ticker failures should not stop processing."""
        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )

        def _fetch(ticker):
            if ticker == "FAIL":
                raise ValueError("Simulated error")
            return {"ticker": ticker}

        tickers = ["AAPL", "FAIL", "GOOGL"]
        results = client._iterate_with_rate_limit(tickers, _fetch, "test")
        assert len(results) == 2
        assert results[0]["ticker"] == "AAPL"
        assert results[1]["ticker"] == "GOOGL"

    def test_list_return_flattened(self):
        """fetch_fn returning a list should be flattened."""
        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client._iterate_with_rate_limit(
            ["AAPL"],
            lambda t: [{"ticker": t, "n": 1}, {"ticker": t, "n": 2}],
            "test",
        )
        assert len(results) == 2

    def test_none_return_skipped(self):
        """fetch_fn returning None should be skipped."""
        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client._iterate_with_rate_limit(
            ["AAPL", "MSFT"],
            lambda t: None if t == "MSFT" else {"ticker": t},
            "test",
        )
        assert len(results) == 1

    def test_empty_ticker_list(self):
        """Empty ticker list should return empty results."""
        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client._iterate_with_rate_limit([], lambda t: {"ticker": t}, "test")
        assert results == []


# ============================================================================
# Tests: fetch_fundamentals
# ============================================================================


class TestFetchFundamentals:
    """Test fundamental data extraction from yfinance."""

    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_extracts_all_fields(self, mock_ticker_cls):
        """Should extract all FUNDAMENTALS_KEYS from info dict."""
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "regularMarketPrice": 150.0,
            "marketCap": 2500000000000,
            "trailingPE": 28.5,
            "forwardPE": 25.0,
            "priceToSalesTrailing12Months": 7.5,
            "priceToBook": 40.2,
            "enterpriseToEbitda": 22.3,
            "profitMargins": 0.26,
            "operatingMargins": 0.31,
            "returnOnEquity": 1.75,
            "totalRevenue": 394328000000,
            "revenueGrowth": 0.08,
            "trailingEps": 6.42,
            "debtToEquity": 176.3,
            "currentRatio": 0.94,
            "dividendYield": 0.005,
            "beta": 1.24,
        }
        mock_ticker.get_earnings_estimate.return_value = None
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client.fetch_fundamentals(["AAPL"])

        assert len(results) == 1
        r = results[0]
        assert r["ticker"] == "AAPL"
        assert r["market_cap"] == 2500000000000
        assert r["pe_ratio"] == 28.5
        assert r["beta"] == 1.24

    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_missing_fields_return_none(self, mock_ticker_cls):
        """Missing fields in info should be None in output."""
        mock_ticker = MagicMock()
        mock_ticker.info = {"regularMarketPrice": 100.0}
        mock_ticker.get_earnings_estimate.return_value = None
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client.fetch_fundamentals(["AAPL"])

        assert len(results) == 1
        assert results[0]["pe_ratio"] is None
        assert results[0]["market_cap"] is None

    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_empty_info_returns_none(self, mock_ticker_cls):
        """Ticker with no regularMarketPrice should be skipped."""
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client.fetch_fundamentals(["DELISTED"])

        assert len(results) == 0


# ============================================================================
# Tests: fetch_analyst_ratings
# ============================================================================


class TestFetchAnalystRatings:
    """Test analyst ratings extraction from yfinance."""

    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_extracts_ratings(self, mock_ticker_cls):
        """Should extract rating data from upgrades_downgrades."""
        import pandas as pd

        mock_ticker = MagicMock()
        mock_df = pd.DataFrame(
            {
                "Firm": ["Goldman Sachs", "Morgan Stanley"],
                "ToGrade": ["Buy", "Overweight"],
                "FromGrade": ["Hold", "Equal-Weight"],
                "Action": ["up", "up"],
            },
            index=pd.to_datetime(["2026-04-10", "2026-04-11"]),
        )
        type(mock_ticker).upgrades_downgrades = PropertyMock(return_value=mock_df)
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client.fetch_analyst_ratings(["AAPL"], lookback_days=30)

        assert len(results) == 2
        assert results[0]["firm"] == "Goldman Sachs"
        assert results[0]["rating_new"] == "Buy"
        assert results[0]["action"] == "up"
        assert results[0]["ticker"] == "AAPL"

    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_empty_ratings(self, mock_ticker_cls):
        """Ticker with no ratings should return empty list."""
        mock_ticker = MagicMock()
        type(mock_ticker).upgrades_downgrades = PropertyMock(return_value=None)
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client.fetch_analyst_ratings(["AAPL"])
        assert len(results) == 0

    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_lookback_filter(self, mock_ticker_cls):
        """Old ratings beyond lookback should be filtered out."""
        import pandas as pd

        mock_ticker = MagicMock()
        mock_df = pd.DataFrame(
            {
                "Firm": ["Old Firm", "New Firm"],
                "ToGrade": ["Buy", "Hold"],
                "FromGrade": ["", ""],
                "Action": ["init", "init"],
            },
            index=pd.to_datetime(["2020-01-01", "2026-04-10"]),
        )
        type(mock_ticker).upgrades_downgrades = PropertyMock(return_value=mock_df)
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client.fetch_analyst_ratings(["AAPL"], lookback_days=30)

        assert len(results) == 1
        assert results[0]["firm"] == "New Firm"


# ============================================================================
# Tests: fetch_earnings_dates
# ============================================================================


class TestFetchEarningsDates:
    """Test earnings dates extraction from yfinance."""

    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_extracts_earnings_data(self, mock_ticker_cls):
        """Should extract EPS estimates and surprises."""
        import pandas as pd

        mock_ticker = MagicMock()
        mock_df = pd.DataFrame(
            {
                "EPS Estimate": [1.50, 1.60],
                "Reported EPS": [1.55, None],
                "Surprise(%)": [3.33, None],
            },
            index=pd.to_datetime(["2026-04-15", "2026-07-15"]),
        )
        mock_ticker.get_earnings_dates.return_value = mock_df
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client.fetch_earnings_dates(["AAPL"], limit=4)

        assert len(results) == 2
        assert results[0]["ticker"] == "AAPL"
        assert results[0]["eps_estimate"] == 1.50
        assert results[0]["eps_actual"] == 1.55
        assert results[0]["surprise_pct"] == 3.33
        assert results[0]["earnings_date"] == date(2026, 4, 15)

    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_empty_earnings(self, mock_ticker_cls):
        """Ticker with no earnings dates should return empty list."""
        mock_ticker = MagicMock()
        mock_ticker.get_earnings_dates.return_value = None
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client.fetch_earnings_dates(["AAPL"])
        assert len(results) == 0

    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_nan_eps_values_cleaned(self, mock_ticker_cls):
        """NaN values in EPS fields should be cleaned to None."""
        import pandas as pd
        import numpy as np

        mock_ticker = MagicMock()
        mock_df = pd.DataFrame(
            {
                "EPS Estimate": [np.nan],
                "Reported EPS": [np.nan],
                "Surprise(%)": [np.nan],
            },
            index=pd.to_datetime(["2026-07-15"]),
        )
        mock_ticker.get_earnings_dates.return_value = mock_df
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        results = client.fetch_earnings_dates(["AAPL"])

        assert len(results) == 1
        assert results[0]["eps_estimate"] is None
        assert results[0]["eps_actual"] is None
        assert results[0]["surprise_pct"] is None
