"""Tests for the GapDetector."""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from trading_signals.collectors.gap_detector import (
    GapDetector,
    GapRepairResult,
    _safe_float,
    _safe_int,
)


class TestSafeConversions:
    """Test helper functions for safe type conversion."""

    def test_safe_float_normal(self):
        assert _safe_float(42.5) == 42.5

    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_nan(self):
        assert _safe_float(float("nan")) is None

    def test_safe_float_string(self):
        assert _safe_float("not_a_number") is None

    def test_safe_int_normal(self):
        assert _safe_int(1000) == 1000

    def test_safe_int_float(self):
        assert _safe_int(1000.7) == 1000

    def test_safe_int_none(self):
        assert _safe_int(None) is None

    def test_safe_int_nan(self):
        assert _safe_int(float("nan")) is None


class TestGapDetector:
    """Test gap detection logic."""

    @patch("trading_signals.collectors.gap_detector.mcal")
    def test_get_expected_trading_days(self, mock_mcal):
        """Should return trading days from NYSE calendar."""
        mock_calendar = MagicMock()
        mock_mcal.get_calendar.return_value = mock_calendar

        # Simulate a week with Mon-Fri trading days
        schedule_index = pd.DatetimeIndex([
            pd.Timestamp("2026-04-06"),  # Monday
            pd.Timestamp("2026-04-07"),
            pd.Timestamp("2026-04-08"),
            pd.Timestamp("2026-04-09"),
            pd.Timestamp("2026-04-10"),  # Friday
        ])
        mock_calendar.schedule.return_value = pd.DataFrame(
            index=schedule_index,
            data={"market_open": schedule_index, "market_close": schedule_index},
        )

        session = MagicMock()
        detector = GapDetector(session)

        days = detector.get_expected_trading_days(
            date(2026, 4, 6), date(2026, 4, 10)
        )

        assert len(days) == 5
        assert all(isinstance(d, date) for d in days)

    def test_detect_gaps_no_data(self):
        """Ticker with no data should return empty gaps list."""
        session = MagicMock()
        # Simulate no data in DB
        session.execute.return_value.one.return_value = (None, None)

        with patch("trading_signals.collectors.gap_detector.mcal"):
            detector = GapDetector(session)
            gaps = detector.detect_gaps("AAPL")

        assert gaps == []

    def test_gap_repair_result_default(self):
        """GapRepairResult should start with all zeros."""
        result = GapRepairResult()
        assert result.gaps_detected == 0
        assert result.gaps_repaired == 0
        assert result.gaps_extrapolated == 0
        assert result.gaps_unfixable == 0
        assert result.details == {}

    @patch("trading_signals.collectors.gap_detector.mcal")
    def test_repair_gaps_empty(self, mock_mcal):
        """No gaps means no repairs needed."""
        session = MagicMock()
        detector = GapDetector(session)

        result = detector.repair_gaps({})
        assert result.gaps_detected == 0
        assert result.gaps_repaired == 0

    @patch("trading_signals.collectors.gap_detector.mcal")
    def test_extrapolate_sets_flag(self, mock_mcal):
        """Extrapolated rows should have is_extrapolated=True."""
        session = MagicMock()

        # Mock: last known price
        mock_price = MagicMock()
        mock_price.close = 150.0
        mock_price.adj_close = 148.0
        session.execute.return_value.scalar_one_or_none.return_value = mock_price

        detector = GapDetector(session)
        count = detector._extrapolate("AAPL", [date(2026, 4, 7)])

        assert count == 1
        # Verify the INSERT was called
        session.execute.assert_called()
        session.flush.assert_called()

    @patch("trading_signals.collectors.gap_detector.mcal")
    def test_extrapolate_no_prior_data(self, mock_mcal):
        """Cannot extrapolate without prior data."""
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        detector = GapDetector(session)
        count = detector._extrapolate("NEW_TICKER", [date(2026, 4, 7)])

        assert count == 0
