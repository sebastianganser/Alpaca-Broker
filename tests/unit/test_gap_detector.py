"""Tests for the GapDetector."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
from sqlalchemy.dialects import postgresql

from trading_signals.collectors._parsing import safe_float as _safe_float
from trading_signals.collectors._parsing import safe_int as _safe_int
from trading_signals.collectors.gap_detector import (
    GapDetector,
    GapRepairResult,
)


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


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


def _weekday_schedule(mock_mcal, start: date, end: date):
    """Configure the mocked NYSE calendar to return all weekdays."""
    cal = MagicMock()
    mock_mcal.get_calendar.return_value = cal

    def schedule(start_date, end_date):
        idx = pd.bdate_range(start_date, end_date)
        return pd.DataFrame(index=idx, data={"market_open": idx, "market_close": idx})

    cal.schedule.side_effect = schedule
    return cal


class TestGapDetectorBulk:
    """M2: grouped detection, safe extrapolation, replaceable extrapolations."""

    @patch("trading_signals.collectors.gap_detector.mcal")
    def test_detect_gaps_bulk_single_grouped_query(self, mock_mcal):
        _weekday_schedule(mock_mcal, date(2026, 4, 6), date(2026, 4, 10))
        session = MagicMock()
        stats = MagicMock()
        # AAPL complete (5 rows Mon-Fri), MSFT missing Wednesday
        stats.all.return_value = [
            ("AAPL", date(2026, 4, 6), date(2026, 4, 10), 5),
            ("MSFT", date(2026, 4, 6), date(2026, 4, 10), 4),
        ]
        detail = MagicMock()
        detail.all.return_value = [
            ("MSFT", date(2026, 4, 6)),
            ("MSFT", date(2026, 4, 7)),
            ("MSFT", date(2026, 4, 9)),
            ("MSFT", date(2026, 4, 10)),
        ]
        session.execute.side_effect = [stats, detail]

        detector = GapDetector(session)
        gaps = detector.detect_gaps_bulk(
            ["AAPL", "MSFT"], start_bound=date(2026, 1, 1), end=date(2026, 4, 10)
        )

        assert gaps == {"MSFT": [date(2026, 4, 8)]}
        # 1 grouped query + 1 detail query (only for MSFT), not N+1
        assert session.execute.call_count == 2
        assert "GROUP BY" in _sql(session.execute.call_args_list[0][0][0])

    @patch("trading_signals.collectors.gap_detector.mcal")
    def test_failed_fetch_never_extrapolates(self, mock_mcal):
        session = MagicMock()
        detector = GapDetector(session)
        detector._extrapolate = MagicMock(return_value=1)

        def boom(tickers, start, end):
            raise RuntimeError("HTTP 500")

        old = date(2026, 1, 5)
        result = detector.repair_gaps(
            {"AAPL": [old]}, batch_fetch_fn=boom, today=date(2026, 4, 1)
        )
        detector._extrapolate.assert_not_called()
        assert result.gaps_extrapolated == 0
        assert result.gaps_unfixable == 1

    @patch("trading_signals.collectors.gap_detector.mcal")
    def test_recent_gaps_not_extrapolated_on_empty_answer(self, mock_mcal):
        session = MagicMock()
        detector = GapDetector(session)
        detector._extrapolate = MagicMock(side_effect=lambda t, dates: len(dates))

        today = date(2026, 4, 20)
        recent = today - timedelta(days=2)
        old = today - timedelta(days=30)
        result = detector.repair_gaps(
            {"AAPL": [old, recent]},
            batch_fetch_fn=lambda tickers, s, e: {},
            min_extrapolate_age_days=7,
            today=today,
        )
        detector._extrapolate.assert_called_once_with("AAPL", [old])
        assert result.gaps_extrapolated == 1

    @patch("trading_signals.collectors.gap_detector.mcal")
    def test_fetched_rows_replace_only_extrapolated(self, mock_mcal):
        session = MagicMock()
        detector = GapDetector(session, source="alpaca")
        d = date(2026, 4, 8)
        df = pd.DataFrame(
            [{"Open": 1, "High": 2, "Low": 0.5, "Close": 1.5, "Adj Close": 1.5,
              "Volume": 100}],
            index=[pd.Timestamp(d)],
        )
        result = detector.repair_gaps(
            {"AAPL": [d]},
            batch_fetch_fn=lambda tickers, s, e: {"AAPL": df},
            today=date(2026, 5, 1),
        )
        assert result.gaps_repaired == 1
        sql = _sql(session.execute.call_args_list[0][0][0])
        assert "ON CONFLICT (ticker, trade_date) DO UPDATE" in sql
        assert "prices_daily.is_extrapolated IS true" in sql


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
