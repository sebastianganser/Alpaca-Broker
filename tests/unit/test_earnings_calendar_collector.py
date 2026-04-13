"""Tests for EarningsCalendarCollector – fetch, store, and UPSERT logic."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors.earnings_calendar_collector import (
    EarningsCalendarCollector,
    _UPSERT_COLUMNS,
)
from trading_signals.db.models.fundamentals import EarningsCalendar


# ============================================================================
# Tests: EarningsCalendar ORM Model
# ============================================================================


class TestEarningsCalendarModel:
    """Test the EarningsCalendar SQLAlchemy model."""

    def test_create_instance(self):
        entry = EarningsCalendar(
            ticker="AAPL",
            earnings_date=date(2026, 4, 24),
            eps_estimate=1.50,
            eps_actual=1.55,
            surprise_pct=3.33,
        )
        assert entry.ticker == "AAPL"
        assert entry.eps_estimate == 1.50
        assert entry.surprise_pct == 3.33

    def test_repr(self):
        entry = EarningsCalendar(
            ticker="MSFT",
            earnings_date=date(2026, 7, 15),
            eps_estimate=2.80,
            eps_actual=None,
        )
        repr_str = repr(entry)
        assert "MSFT" in repr_str
        assert "2.8" in repr_str

    def test_nullable_fields(self):
        entry = EarningsCalendar(
            ticker="AAPL",
            earnings_date=date(2026, 7, 15),
        )
        assert entry.eps_estimate is None
        assert entry.eps_actual is None
        assert entry.surprise_pct is None
        assert entry.time_of_day is None
        assert entry.revenue_estimate is None

    def test_table_name(self):
        assert EarningsCalendar.__tablename__ == "earnings_calendar"

    def test_schema(self):
        assert EarningsCalendar.__table_args__[-1]["schema"] == "signals"


# ============================================================================
# Tests: EarningsCalendarCollector
# ============================================================================


class TestEarningsCalendarCollector:
    """Test EarningsCalendarCollector with mocked dependencies."""

    def test_name(self):
        collector = EarningsCalendarCollector()
        assert collector.name == "earnings_calendar_collector"

    def test_default_earnings_limit(self):
        collector = EarningsCalendarCollector()
        assert collector.earnings_limit == 4

    def test_custom_earnings_limit(self):
        collector = EarningsCalendarCollector(earnings_limit=8)
        assert collector.earnings_limit == 8

    @patch("trading_signals.collectors.earnings_calendar_collector.YFinanceClient")
    def test_fetch_loads_active_tickers(self, mock_client_cls):
        """fetch() should query universe for active tickers."""
        mock_client = MagicMock()
        mock_client.fetch_earnings_dates.return_value = []
        mock_client_cls.return_value = mock_client

        collector = EarningsCalendarCollector(earnings_limit=4)
        collector.client = mock_client

        session = MagicMock()
        session.execute.return_value.all.return_value = [
            ("AAPL",), ("MSFT",), ("GOOGL",)
        ]

        collector.fetch(session)

        mock_client.fetch_earnings_dates.assert_called_once_with(
            ["AAPL", "MSFT", "GOOGL"], limit=4
        )

    def test_store_writes_records(self):
        """store() should write records with UPSERT."""
        collector = EarningsCalendarCollector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = [
            {
                "ticker": "AAPL",
                "earnings_date": date(2026, 4, 24),
                "time_of_day": "AMC",
                "eps_estimate": 1.50,
                "eps_actual": 1.55,
                "revenue_estimate": None,
                "revenue_actual": None,
                "surprise_pct": 3.33,
            }
        ]

        fetched, written = collector.store(session, data)

        assert fetched == 1
        assert written == 1
        session.execute.assert_called_once()
        session.flush.assert_called()

    def test_store_empty_data(self):
        """store() with empty data should return zero counts."""
        collector = EarningsCalendarCollector()
        session = MagicMock()

        fetched, written = collector.store(session, [])

        assert fetched == 0
        assert written == 0
        session.flush.assert_called()

    def test_store_multiple_records(self):
        """store() should handle multiple earnings entries."""
        collector = EarningsCalendarCollector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = [
            {
                "ticker": "AAPL",
                "earnings_date": date(2026, 4, 24),
                "time_of_day": None,
                "eps_estimate": 1.50,
                "eps_actual": None,
                "revenue_estimate": None,
                "revenue_actual": None,
                "surprise_pct": None,
            },
            {
                "ticker": "AAPL",
                "earnings_date": date(2026, 7, 15),
                "time_of_day": None,
                "eps_estimate": 1.60,
                "eps_actual": None,
                "revenue_estimate": None,
                "revenue_actual": None,
                "surprise_pct": None,
            },
        ]

        fetched, written = collector.store(session, data)

        assert fetched == 2
        assert written == 2
        assert session.execute.call_count == 2

    def test_store_future_earnings_no_actual(self):
        """Future earnings should have None for eps_actual and surprise_pct."""
        collector = EarningsCalendarCollector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = [
            {
                "ticker": "MSFT",
                "earnings_date": date(2026, 10, 20),
                "time_of_day": None,
                "eps_estimate": 3.10,
                "eps_actual": None,
                "revenue_estimate": None,
                "revenue_actual": None,
                "surprise_pct": None,
            }
        ]

        fetched, written = collector.store(session, data)
        assert fetched == 1
        assert written == 1

    def test_upsert_columns_match_model(self):
        """All UPSERT columns should exist in the ORM model."""
        model_columns = {c.name for c in EarningsCalendar.__table__.columns}
        for col in _UPSERT_COLUMNS:
            assert col in model_columns, f"Column {col} not found in model"
