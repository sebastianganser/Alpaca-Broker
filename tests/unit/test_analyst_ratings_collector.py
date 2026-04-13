"""Tests for AnalystRatingsCollector – fetch, store, and dedup logic."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors.analyst_ratings_collector import (
    AnalystRatingsCollector,
)
from trading_signals.db.models.fundamentals import AnalystRating


# ============================================================================
# Tests: AnalystRating ORM Model
# ============================================================================


class TestAnalystRatingModel:
    """Test the AnalystRating SQLAlchemy model."""

    def test_create_instance(self):
        rating = AnalystRating(
            ticker="AAPL",
            firm="Goldman Sachs",
            rating_date=date(2026, 4, 10),
            rating_new="Buy",
            rating_old="Hold",
            action="up",
        )
        assert rating.ticker == "AAPL"
        assert rating.firm == "Goldman Sachs"
        assert rating.action == "up"

    def test_repr(self):
        rating = AnalystRating(
            ticker="MSFT",
            firm="Morgan Stanley",
            action="down",
            rating_date=date(2026, 4, 11),
        )
        repr_str = repr(rating)
        assert "MSFT" in repr_str
        assert "Morgan Stanley" in repr_str
        assert "down" in repr_str

    def test_nullable_fields(self):
        rating = AnalystRating(ticker="AAPL")
        assert rating.firm is None
        assert rating.analyst is None
        assert rating.price_target_new is None
        assert rating.price_target_old is None

    def test_table_name(self):
        assert AnalystRating.__tablename__ == "analyst_ratings"

    def test_schema(self):
        assert AnalystRating.__table_args__[-1]["schema"] == "signals"


# ============================================================================
# Tests: AnalystRatingsCollector
# ============================================================================


class TestAnalystRatingsCollector:
    """Test AnalystRatingsCollector with mocked dependencies."""

    def test_name(self):
        collector = AnalystRatingsCollector()
        assert collector.name == "analyst_ratings_collector"

    def test_default_lookback(self):
        collector = AnalystRatingsCollector()
        assert collector.lookback_days == 30

    def test_custom_lookback(self):
        collector = AnalystRatingsCollector(lookback_days=60)
        assert collector.lookback_days == 60

    @patch("trading_signals.collectors.analyst_ratings_collector.YFinanceClient")
    def test_fetch_loads_active_tickers(self, mock_client_cls):
        """fetch() should query universe for active tickers."""
        mock_client = MagicMock()
        mock_client.fetch_analyst_ratings.return_value = []
        mock_client_cls.return_value = mock_client

        collector = AnalystRatingsCollector(lookback_days=30)
        collector.client = mock_client

        session = MagicMock()
        session.execute.return_value.all.return_value = [
            ("AAPL",), ("MSFT",)
        ]

        collector.fetch(session)

        mock_client.fetch_analyst_ratings.assert_called_once_with(
            ["AAPL", "MSFT"], lookback_days=30
        )

    def test_store_writes_records(self):
        """store() should write records with ON CONFLICT DO NOTHING."""
        collector = AnalystRatingsCollector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = [
            {
                "ticker": "AAPL",
                "firm": "Goldman Sachs",
                "analyst": None,
                "rating_date": date(2026, 4, 10),
                "rating_new": "Buy",
                "rating_old": "Hold",
                "price_target_new": None,
                "price_target_old": None,
                "action": "up",
                "raw_data": {"Firm": "Goldman Sachs"},
            }
        ]

        fetched, written = collector.store(session, data)

        assert fetched == 1
        assert written == 1
        session.execute.assert_called_once()
        session.flush.assert_called()

    def test_store_dedup_counts(self):
        """Duplicate records should not increment written count."""
        collector = AnalystRatingsCollector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0  # duplicate
        session.execute.return_value = mock_result

        data = [
            {
                "ticker": "AAPL",
                "firm": "Goldman Sachs",
                "analyst": None,
                "rating_date": date(2026, 4, 10),
                "rating_new": "Buy",
                "rating_old": "Hold",
                "price_target_new": None,
                "price_target_old": None,
                "action": "up",
                "raw_data": {},
            }
        ]

        fetched, written = collector.store(session, data)
        assert fetched == 1
        assert written == 0

    def test_store_empty_data(self):
        """store() with empty data should return zero counts."""
        collector = AnalystRatingsCollector()
        session = MagicMock()

        fetched, written = collector.store(session, [])

        assert fetched == 0
        assert written == 0
        session.flush.assert_called()

    def test_store_multiple_records(self):
        """store() should handle multiple records correctly."""
        collector = AnalystRatingsCollector()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = [
            {
                "ticker": "AAPL",
                "firm": "Goldman Sachs",
                "analyst": None,
                "rating_date": date(2026, 4, 10),
                "rating_new": "Buy",
                "rating_old": "Hold",
                "price_target_new": None,
                "price_target_old": None,
                "action": "up",
                "raw_data": {},
            },
            {
                "ticker": "MSFT",
                "firm": "Morgan Stanley",
                "analyst": None,
                "rating_date": date(2026, 4, 11),
                "rating_new": "Overweight",
                "rating_old": "Equal-Weight",
                "price_target_new": None,
                "price_target_old": None,
                "action": "up",
                "raw_data": {},
            },
        ]

        fetched, written = collector.store(session, data)

        assert fetched == 2
        assert written == 2
        assert session.execute.call_count == 2
