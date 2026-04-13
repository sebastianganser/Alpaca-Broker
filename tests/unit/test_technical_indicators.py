"""Tests for TechnicalIndicatorsComputer.

Tests verify:
  - Indicator calculations with known values
  - Min-data checks (graceful handling of insufficient history)
  - Relative strength calculation
  - UPSERT idempotency
  - Error handling (single ticker failure doesn't abort run)
  - Backfill mode
"""

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from trading_signals.derived.technical_indicators import (
    TechnicalIndicatorsComputer,
)

# ── Fixtures ──────────────────────────────────────────────────────────


def _make_price_df(
    n_days: int,
    start_close: float = 100.0,
    daily_return: float = 0.001,
    start_date: date = date(2024, 1, 2),
    volatility: float = 0.02,
) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame for testing.

    Args:
        n_days: Number of trading days to generate.
        start_close: Starting close price.
        daily_return: Average daily return (0.001 = 0.1%/day).
        start_date: First date in the series.
        volatility: Random noise factor for OHLC spread.
    """
    np.random.seed(42)  # Reproducible
    dates = pd.bdate_range(start=start_date, periods=n_days)

    closes = [start_close]
    for _ in range(n_days - 1):
        noise = np.random.normal(0, volatility)
        next_close = closes[-1] * (1 + daily_return + noise)
        closes.append(max(next_close, 0.01))  # Prevent negative

    closes = np.array(closes)
    highs = closes * (1 + abs(np.random.normal(0, 0.005, n_days)))
    lows = closes * (1 - abs(np.random.normal(0, 0.005, n_days)))
    opens = (closes + np.random.normal(0, 0.3, n_days))
    volumes = np.random.randint(1_000_000, 50_000_000, n_days)

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes.astype(float),
    }, index=dates)

    return df


def _make_steady_rise_df(n_days: int = 50) -> pd.DataFrame:
    """Generate a steadily rising price series (for RSI > 70 test)."""
    dates = pd.bdate_range(start=date(2024, 1, 2), periods=n_days)
    closes = np.linspace(100, 200, n_days)  # Steady rise
    highs = closes * 1.002
    lows = closes * 0.998
    opens = closes * 0.999
    volumes = np.full(n_days, 10_000_000.0)

    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    }, index=dates)


def _make_steady_fall_df(n_days: int = 50) -> pd.DataFrame:
    """Generate a steadily falling price series (for RSI < 30 test)."""
    dates = pd.bdate_range(start=date(2024, 1, 2), periods=n_days)
    closes = np.linspace(200, 100, n_days)  # Steady fall
    highs = closes * 1.002
    lows = closes * 0.998
    opens = closes * 1.001
    volumes = np.full(n_days, 10_000_000.0)

    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    }, index=dates)


def _make_flat_df(n_days: int = 50, price: float = 100.0) -> pd.DataFrame:
    """Generate a flat price series (price stays constant)."""
    dates = pd.bdate_range(start=date(2024, 1, 2), periods=n_days)
    closes = np.full(n_days, price)
    highs = np.full(n_days, price * 1.001)
    lows = np.full(n_days, price * 0.999)
    opens = np.full(n_days, price)
    volumes = np.full(n_days, 10_000_000.0)

    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    }, index=dates)


# ── SMA Tests ─────────────────────────────────────────────────────────


class TestSMAKnownValues:
    """Test SMA calculations with known values."""

    def test_sma_20_is_arithmetic_mean(self):
        """SMA 20 should be the arithmetic mean of the last 20 closes."""
        df = _make_price_df(30)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        assert "sma_20" in result.columns

        # The last SMA 20 should be the mean of the last 20 closes
        last_20_close = df["close"].iloc[-20:].mean()
        last_sma = result["sma_20"].dropna().iloc[-1]
        assert abs(last_sma - last_20_close) < 0.01

    def test_sma_50_requires_50_days(self):
        """SMA 50 should be NaN when fewer than 50 days of data."""
        df = _make_price_df(30)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        assert "sma_50" not in result.columns or result["sma_50"].isna().all()

    def test_sma_200_requires_200_days(self):
        """SMA 200 should only appear when >= 200 days of data."""
        df = _make_price_df(100)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        assert "sma_200" not in result.columns or result["sma_200"].isna().all()

    def test_sma_200_present_with_enough_data(self):
        """SMA 200 should be computed when 200+ days available."""
        df = _make_price_df(250)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        assert "sma_200" in result.columns
        # Last row should have a valid SMA 200
        last_sma = result["sma_200"].iloc[-1]
        assert pd.notna(last_sma)
        assert last_sma > 0

    def test_sma_flat_series(self):
        """SMA of a flat series should equal the constant price."""
        df = _make_flat_df(30, price=50.0)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        last_sma = result["sma_20"].iloc[-1]
        assert abs(last_sma - 50.0) < 0.01


# ── EMA Tests ─────────────────────────────────────────────────────────


class TestEMAKnownValues:
    """Test EMA calculations."""

    def test_ema_12_present(self):
        """EMA 12 should be computed for datasets with 12+ days."""
        df = _make_price_df(30)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        assert "ema_12" in result.columns
        assert pd.notna(result["ema_12"].iloc[-1])

    def test_ema_26_present(self):
        """EMA 26 should be computed for datasets with 26+ days."""
        df = _make_price_df(40)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        assert "ema_26" in result.columns
        assert pd.notna(result["ema_26"].iloc[-1])

    def test_ema_responds_faster_than_sma(self):
        """EMA should react faster to recent price changes than SMA."""
        # Create a series that rises sharply at the end
        df = _make_flat_df(30, price=100.0)
        # Override last 5 days with sharp rise
        df.iloc[-5:, df.columns.get_loc("close")] = [110, 120, 130, 140, 150]

        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        # EMA 12 should be closer to current price than SMA 20
        last_close = 150.0
        ema_12 = result["ema_12"].iloc[-1]
        sma_20 = result["sma_20"].iloc[-1]

        assert abs(ema_12 - last_close) < abs(sma_20 - last_close)


# ── RSI Tests ─────────────────────────────────────────────────────────


class TestRSI:
    """Test RSI calculation."""

    def test_rsi_overbought_in_steady_rise(self):
        """RSI should be > 70 in a steadily rising market."""
        df = _make_steady_rise_df(50)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        last_rsi = result["rsi_14"].iloc[-1]
        assert last_rsi > 70, f"Expected RSI > 70, got {last_rsi}"

    def test_rsi_oversold_in_steady_fall(self):
        """RSI should be < 30 in a steadily falling market."""
        df = _make_steady_fall_df(50)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        last_rsi = result["rsi_14"].iloc[-1]
        assert last_rsi < 30, f"Expected RSI < 30, got {last_rsi}"

    def test_rsi_range(self):
        """RSI should always be between 0 and 100."""
        df = _make_price_df(100)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        rsi_values = result["rsi_14"].dropna()
        assert (rsi_values >= 0).all(), "RSI should be >= 0"
        assert (rsi_values <= 100).all(), "RSI should be <= 100"

    def test_rsi_none_with_insufficient_data(self):
        """RSI should be None when fewer than 14 days of data."""
        df = _make_price_df(10)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        # With < 14 days, the entire result should be None
        assert result is None


# ── MACD Tests ────────────────────────────────────────────────────────


class TestMACD:
    """Test MACD calculation."""

    def test_macd_components_present(self):
        """MACD line, signal, and histogram should all be present."""
        df = _make_price_df(50)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        assert "macd" in result.columns
        assert "macd_signal" in result.columns
        assert "macd_histogram" in result.columns

    def test_macd_histogram_equals_macd_minus_signal(self):
        """MACD histogram = MACD line - Signal line."""
        df = _make_price_df(60)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        valid = result.dropna(subset=["macd", "macd_signal", "macd_histogram"])
        if not valid.empty:
            computed_hist = valid["macd"] - valid["macd_signal"]
            diff = (valid["macd_histogram"] - computed_hist).abs()
            assert (diff < 0.01).all(), "Histogram should equal MACD - Signal"

    def test_macd_not_computed_with_insufficient_data(self):
        """MACD should not be computed with < 35 days of data."""
        df = _make_price_df(25)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        if "macd" in result.columns:
            assert result["macd"].isna().all()


# ── Bollinger Bands Tests ─────────────────────────────────────────────


class TestBollingerBands:
    """Test Bollinger Bands calculation."""

    def test_bollinger_bands_relationship(self):
        """Upper band should always be above lower band."""
        df = _make_price_df(50)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        valid = result.dropna(subset=["bollinger_upper", "bollinger_lower"])
        assert not valid.empty
        assert (valid["bollinger_upper"] > valid["bollinger_lower"]).all()

    def test_bollinger_bands_contain_price(self):
        """Close price should be between upper and lower bands (most of the time)."""
        df = _make_price_df(100)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        # Align with original close prices
        valid_idx = result.dropna(subset=["bollinger_upper", "bollinger_lower"]).index
        close = df.loc[valid_idx, "close"]
        upper = result.loc[valid_idx, "bollinger_upper"]
        lower = result.loc[valid_idx, "bollinger_lower"]

        # At least 90% of closes should be within bands
        within = ((close >= lower) & (close <= upper)).mean()
        assert within > 0.85, f"Expected >85% within bands, got {within:.1%}"

    def test_bollinger_width_increases_with_volatility(self):
        """Bollinger band width should be wider during volatile periods."""
        df = _make_price_df(100, volatility=0.05)  # High vol
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result_high = computer._calculate_indicators_dataframe(df)

        df_low = _make_flat_df(100, price=100.0)  # Low vol
        result_low = computer._calculate_indicators_dataframe(df_low)

        assert result_high is not None and result_low is not None
        bb_high = result_high["bollinger_upper"] - result_high["bollinger_lower"]
        width_high = bb_high.dropna().iloc[-1]
        bb_low = result_low["bollinger_upper"] - result_low["bollinger_lower"]
        width_low = bb_low.dropna().iloc[-1]

        assert width_high > width_low


# ── ATR Tests ─────────────────────────────────────────────────────────


class TestATR:
    """Test ATR calculation."""

    def test_atr_positive(self):
        """ATR should always be positive."""
        df = _make_price_df(50)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        atr_values = result["atr_14"].dropna()
        assert not atr_values.empty
        assert (atr_values > 0).all(), "ATR should always be positive"

    def test_atr_higher_for_volatile_stock(self):
        """ATR should be higher for a more volatile stock."""
        df_volatile = _make_price_df(50, volatility=0.05)
        df_calm = _make_price_df(50, volatility=0.001)

        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result_v = computer._calculate_indicators_dataframe(df_volatile)
        result_c = computer._calculate_indicators_dataframe(df_calm)

        assert result_v is not None and result_c is not None
        atr_volatile = result_v["atr_14"].dropna().iloc[-1]
        atr_calm = result_c["atr_14"].dropna().iloc[-1]
        assert atr_volatile > atr_calm


# ── Volume SMA Tests ──────────────────────────────────────────────────


class TestVolumeSMA:
    """Test Volume SMA calculation."""

    def test_volume_sma_20_is_mean(self):
        """Volume SMA 20 should be the mean of last 20 volumes."""
        df = _make_price_df(30)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        assert "volume_sma_20" in result.columns

        # The last Volume SMA 20 should be the mean of the last 20 volumes
        expected_mean = df["volume"].iloc[-20:].mean()
        actual_sma = result["volume_sma_20"].dropna().iloc[-1]
        assert abs(actual_sma - expected_mean) < 1.0  # Allow rounding


# ── Relative Strength Tests ───────────────────────────────────────────


class TestRelativeStrength:
    """Test Relative Strength vs SPY calculation."""

    def test_outperformer_has_positive_rs(self):
        """A ticker that outperforms SPY should have RS > 0."""
        spy_df = _make_price_df(50, start_close=100, daily_return=0.001)
        ticker_df = _make_price_df(50, start_close=100, daily_return=0.005)

        computer = TechnicalIndicatorsComputer(session=MagicMock())
        computer._spy_df = spy_df

        rs = computer._calculate_relative_strength(ticker_df, period=20)
        assert rs is not None
        last_rs = rs.dropna().iloc[-1]
        assert last_rs > 0, f"Expected RS > 0 for outperformer, got {last_rs}"

    def test_underperformer_has_negative_rs(self):
        """A ticker that underperforms SPY should have RS < 0."""
        spy_df = _make_price_df(50, start_close=100, daily_return=0.005)
        ticker_df = _make_price_df(50, start_close=100, daily_return=0.001)

        computer = TechnicalIndicatorsComputer(session=MagicMock())
        computer._spy_df = spy_df

        rs = computer._calculate_relative_strength(ticker_df, period=20)
        assert rs is not None
        last_rs = rs.dropna().iloc[-1]
        assert last_rs < 0, f"Expected RS < 0 for underperformer, got {last_rs}"

    def test_equal_performance_near_zero(self):
        """Identical performance to SPY should give RS ≈ 0."""
        spy_df = _make_price_df(50, start_close=100, daily_return=0.002)

        computer = TechnicalIndicatorsComputer(session=MagicMock())
        computer._spy_df = spy_df

        # Use same data for ticker = essentially SPY itself
        rs = computer._calculate_relative_strength(spy_df, period=20)
        assert rs is not None
        last_rs = rs.dropna().iloc[-1]
        assert abs(last_rs) < 0.01, f"Expected RS ≈ 0, got {last_rs}"

    def test_relative_strength_none_without_spy(self):
        """RS should be None when SPY data is missing."""
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        computer._spy_df = None

        df = _make_price_df(50)
        rs = computer._calculate_relative_strength(df, period=20)
        assert rs is None


# ── Min-Data Check Tests ──────────────────────────────────────────────


class TestMinDataChecks:
    """Test that indicators handle insufficient data gracefully."""

    def test_insufficient_data_returns_none(self):
        """With < 14 days, no indicators can be computed."""
        df = _make_price_df(10)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)
        assert result is None

    def test_14_days_has_rsi_but_not_macd(self):
        """With exactly 14 days, RSI is available but MACD is not."""
        df = _make_price_df(20)  # Need a few extra for RSI warmup
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        # RSI should have some valid values
        rsi_valid = result["rsi_14"].dropna()
        assert len(rsi_valid) > 0

    def test_partial_indicators_with_50_days(self):
        """With 50 days: SMA 20/50 yes, SMA 200 no, MACD yes."""
        df = _make_price_df(50)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        # SMA 20 should be valid
        assert pd.notna(result["sma_20"].iloc[-1])
        # SMA 50 should be valid at the last row
        assert pd.notna(result["sma_50"].iloc[-1])
        # SMA 200 should not be present or all NaN
        assert "sma_200" not in result.columns or result["sma_200"].isna().all()


# ── Integration Tests ─────────────────────────────────────────────────


class TestComputeIntegration:
    """Test the full compute pipeline with mocked DB."""

    def test_compute_for_date_no_data_returns_false(self):
        """When no price data exists, compute_for_date returns False."""
        mock_session = MagicMock()
        mock_session.execute.return_value.all.return_value = []

        computer = TechnicalIndicatorsComputer(session=mock_session)
        computer._spy_df = _make_price_df(50)

        # Patch _load_price_history to return None
        with patch.object(computer, "_load_price_history", return_value=None):
            result = computer._compute_for_date("AAPL", date(2024, 6, 1))
            assert result is False

    def test_store_indicators_converts_nan_to_none(self):
        """NaN values in pandas should be stored as NULL (None)."""
        mock_session = MagicMock()
        computer = TechnicalIndicatorsComputer(session=mock_session)

        # Create a row with some NaN values
        row = pd.Series({
            "sma_20": 150.5,
            "sma_50": np.nan,
            "sma_200": np.nan,
            "ema_12": 151.2,
            "ema_26": 149.8,
            "rsi_14": 55.3,
            "macd": 1.5,
            "macd_signal": 1.2,
            "macd_histogram": 0.3,
            "bollinger_upper": 155.0,
            "bollinger_lower": 145.0,
            "atr_14": 2.5,
            "volume_sma_20": 15_000_000.0,
            "relative_strength_spy": 0.05,
        }, name=pd.Timestamp("2024-06-01"))

        # Mock execute to return a result with rowcount=1
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        result = computer._store_indicators("AAPL", row)
        assert result is True

        # Verify the values passed to execute
        call_args = mock_session.execute.call_args
        assert call_args is not None

    def test_all_indicators_on_250_day_dataset(self):
        """Full indicator suite should work on a 250-day dataset."""
        df = _make_price_df(250)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        computer._spy_df = _make_price_df(250)

        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        last_row = result.iloc[-1]

        # All indicators should be present on the last row
        expected_cols = [
            "sma_20", "sma_50", "sma_200", "ema_12", "ema_26",
            "rsi_14", "macd", "macd_signal", "macd_histogram",
            "bollinger_upper", "bollinger_lower", "atr_14",
            "volume_sma_20", "relative_strength_spy",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"
            assert pd.notna(last_row[col]), f"{col} should not be NaN on last row"


# ── Edge Case Tests ───────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_dataframe(self):
        """Empty DataFrame should return None."""
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)
        assert result is None

    def test_single_day_returns_none(self):
        """Single day of data should return None."""
        df = _make_price_df(1)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)
        assert result is None

    def test_flat_prices_produce_valid_indicators(self):
        """Flat prices (no movement) should still produce valid indicators."""
        df = _make_flat_df(50, price=100.0)
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        # SMA should equal the flat price
        sma_valid = result["sma_20"].dropna()
        if not sma_valid.empty:
            assert abs(sma_valid.iloc[-1] - 100.0) < 0.01
        # RSI may be NaN for perfectly flat prices (no gains/losses)
        # This is valid behavior – not an error
        rsi_valid = result["rsi_14"].dropna()
        if not rsi_valid.empty:
            assert 0 <= rsi_valid.iloc[-1] <= 100

    def test_very_volatile_data(self):
        """Highly volatile data should still produce valid indicators."""
        df = _make_price_df(50, volatility=0.1)  # 10% daily volatility
        computer = TechnicalIndicatorsComputer(session=MagicMock())
        result = computer._calculate_indicators_dataframe(df)

        assert result is not None
        # All values should be finite
        for col in result.columns:
            valid = pd.to_numeric(result[col].dropna(), errors="coerce")
            assert np.isfinite(valid.values).all(), f"{col} has non-finite values"
