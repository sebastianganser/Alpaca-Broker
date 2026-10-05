"""Tests for YFinanceClient – rate-limiting, batching, and graceful error handling."""

from datetime import date
from unittest.mock import MagicMock, PropertyMock, patch

from trading_signals.collectors.yfinance_client import (
    YFinanceClient,
    YFRateLimitError,
    _clean_numeric,
    to_yahoo_symbol,
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
        from datetime import datetime, timedelta

        import pandas as pd

        # Use recent dates relative to today to stay within lookback window
        recent_date_1 = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        recent_date_2 = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

        mock_ticker = MagicMock()
        mock_df = pd.DataFrame(
            {
                "Firm": ["Goldman Sachs", "Morgan Stanley"],
                "ToGrade": ["Buy", "Overweight"],
                "FromGrade": ["Hold", "Equal-Weight"],
                "Action": ["up", "up"],
            },
            index=pd.to_datetime([recent_date_1, recent_date_2]),
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
        from datetime import datetime, timedelta

        import pandas as pd

        # One old date (beyond 30 days) and one recent
        old_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        recent_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

        mock_ticker = MagicMock()
        mock_df = pd.DataFrame(
            {
                "Firm": ["Old Firm", "New Firm"],
                "ToGrade": ["Buy", "Hold"],
                "FromGrade": ["", ""],
                "Action": ["init", "init"],
            },
            index=pd.to_datetime([old_date, recent_date]),
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
        import numpy as np
        import pandas as pd

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

    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_time_of_day_and_ny_date(self, mock_ticker_cls):
        """BMO/AMC derived from the ET timestamp; date in New York time."""
        import pandas as pd

        # Index given in UTC; the client must convert to America/New_York.
        idx = pd.DatetimeIndex(
            [
                "2026-04-15 11:00",  # 07:00 EDT → BMO
                "2026-07-15 20:30",  # 16:30 EDT → AMC
                "2026-10-15 17:00",  # 13:00 EDT → DMH
                "2027-01-15 05:00",  # 00:00 EST → unknown
                # 01:00 UTC = 21:00 ET of the previous day → AMC, NY date
                "2027-04-16 01:00",
            ],
            tz="UTC",
        )
        mock_ticker = MagicMock()
        mock_ticker.get_earnings_dates.return_value = pd.DataFrame(
            {"EPS Estimate": [1.0] * 5}, index=idx
        )
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        res = client.fetch_earnings_dates(["AAPL"])

        assert [r["time_of_day"] for r in res] == ["BMO", "AMC", "DMH", None, "AMC"]
        assert res[4]["earnings_date"] == date(2027, 4, 15)


# ============================================================================
# Tests: Symbol mapping, rate limits, outcome callbacks (M4 / H5)
# ============================================================================


class TestSymbolMappingAndRateLimits:
    def test_to_yahoo_symbol(self):
        assert to_yahoo_symbol("BRK.B") == "BRK-B"
        assert to_yahoo_symbol("BF/B") == "BF-B"
        assert to_yahoo_symbol("AAPL") == "AAPL"

    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_dotted_ticker_mapped_but_stored_under_original(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.info = {"regularMarketPrice": 1.0, "mostRecentQuarter": 1782777600}
        mock_ticker.get_earnings_estimate.return_value = None
        mock_ticker.analyst_price_targets = None
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        res = client.fetch_fundamentals(["BRK.B"])

        mock_ticker_cls.assert_called_once_with("BRK-B")
        assert res[0]["ticker"] == "BRK.B"
        assert res[0]["most_recent_quarter"] == date(2026, 6, 30)

    @patch("trading_signals.collectors.yfinance_client.time.sleep")
    def test_rate_limit_retried_with_backoff(self, mock_sleep):
        calls = {"n": 0}

        def _fetch(t):
            calls["n"] += 1
            if calls["n"] < 3:
                raise YFRateLimitError()
            return {"ticker": t}

        client = YFinanceClient(
            batch_size=10,
            delay_between_tickers=0,
            delay_between_batches=0,
            rate_limit_base_delay=10,
        )
        ok, err = [], []
        res = client._iterate_with_rate_limit(
            ["AAPL"], _fetch, "test", lambda: ok.append(1), lambda: err.append(1)
        )

        assert res == [{"ticker": "AAPL"}]
        assert [c.args[0] for c in mock_sleep.call_args_list] == [10, 20]
        assert (len(ok), len(err)) == (1, 0)

    @patch("trading_signals.collectors.yfinance_client.time.sleep")
    def test_rate_limit_exhausted_counts_as_error(self, mock_sleep):
        def _fetch(t):
            raise YFRateLimitError()

        client = YFinanceClient(
            batch_size=10,
            delay_between_tickers=0,
            delay_between_batches=0,
            rate_limit_retries=2,
        )
        ok, err = [], []
        res = client._iterate_with_rate_limit(
            ["AAPL"], _fetch, "test", lambda: ok.append(1), lambda: err.append(1)
        )
        assert res == []
        assert mock_sleep.call_count == 2
        assert (len(ok), len(err)) == (0, 1)

    def test_callbacks_empty_answer_is_success(self):
        client = YFinanceClient(
            batch_size=10, delay_between_tickers=0, delay_between_batches=0
        )
        ok, err = [], []

        def _fetch(t):
            if t == "FAIL":
                raise ValueError("x")
            return None if t == "EMPTY" else {"ticker": t}

        client._iterate_with_rate_limit(
            ["AAPL", "EMPTY", "FAIL"], _fetch, "test",
            lambda: ok.append(1), lambda: err.append(1),
        )
        assert (len(ok), len(err)) == (2, 1)

    @patch("trading_signals.collectors.yfinance_client.time.sleep")
    @patch("trading_signals.collectors.yfinance_client.yf.Ticker")
    def test_estimates_rate_limit_not_swallowed(self, mock_ticker_cls, mock_sleep):
        """Rate limits inside property access must trigger the retry."""
        mock_ticker = MagicMock()
        type(mock_ticker).eps_trend = PropertyMock(side_effect=YFRateLimitError())
        mock_ticker_cls.return_value = mock_ticker

        client = YFinanceClient(
            batch_size=10,
            delay_between_tickers=0,
            delay_between_batches=0,
            rate_limit_retries=1,
        )
        err = []
        res = client.fetch_estimates(["AAPL"], on_error=lambda: err.append(1))
        assert res == []
        assert err == [1]
        assert mock_sleep.call_count == 1
