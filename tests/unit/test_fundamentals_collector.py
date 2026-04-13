"""Tests for FundamentalsCollectorYF – fetch, store, and UPSERT logic."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.collectors.fundamentals_collector import (
    FundamentalsCollectorYF,
    _UPSERT_COLUMNS,
)
from trading_signals.db.models.fundamentals import FundamentalsSnapshot


# ============================================================================
# Tests: FundamentalsSnapshot ORM Model
# ============================================================================


class TestFundamentalsSnapshotModel:
    """Test the FundamentalsSnapshot SQLAlchemy model."""

    def test_create_instance(self):
        snap = FundamentalsSnapshot(
            ticker="AAPL",
            snapshot_date=date(2026, 4, 13),
            market_cap=2500000000000,
            pe_ratio=28.5,
        )
        assert snap.ticker == "AAPL"
        assert snap.pe_ratio == 28.5

    def test_repr(self):
        snap = FundamentalsSnapshot(
            ticker="MSFT",
            snapshot_date=date(2026, 4, 13),
            pe_ratio=35.2,
            market_cap=3000000000000,
        )
        repr_str = repr(snap)
        assert "MSFT" in repr_str
        assert "35.2" in repr_str

    def test_nullable_fields(self):
        snap = FundamentalsSnapshot(
            ticker="AAPL",
            snapshot_date=date(2026, 4, 13),
        )
        assert snap.pe_ratio is None
        assert snap.market_cap is None
        assert snap.beta is None

    def test_table_name(self):
        assert FundamentalsSnapshot.__tablename__ == "fundamentals_snapshot"

    def test_schema(self):
        assert FundamentalsSnapshot.__table_args__[-1]["schema"] == "signals"


# ============================================================================
# Tests: FundamentalsCollectorYF
# ============================================================================


class TestFundamentalsCollector:
    """Test FundamentalsCollectorYF with mocked dependencies."""

    def test_name(self):
        collector = FundamentalsCollectorYF()
        assert collector.name == "fundamentals_yf"

    def test_custom_params(self):
        collector = FundamentalsCollectorYF(
            batch_size=10,
            delay_between_tickers=0.1,
            delay_between_batches=1.0,
        )
        assert collector.client.batch_size == 10

    @patch("trading_signals.collectors.fundamentals_collector.YFinanceClient")
    def test_fetch_loads_active_tickers(self, mock_client_cls):
        """fetch() should query universe for active tickers."""
        mock_client = MagicMock()
        mock_client.fetch_fundamentals.return_value = []
        mock_client_cls.return_value = mock_client

        collector = FundamentalsCollectorYF()
        collector.client = mock_client

        session = MagicMock()
        session.execute.return_value.all.return_value = [
            ("AAPL",), ("MSFT",), ("GOOGL",)
        ]

        collector.fetch(session)

        mock_client.fetch_fundamentals.assert_called_once_with(
            ["AAPL", "MSFT", "GOOGL"]
        )

    def test_store_writes_records(self):
        """store() should write records with UPSERT."""
        collector = FundamentalsCollectorYF()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = [
            {
                "ticker": "AAPL",
                "market_cap": 2500000000000,
                "pe_ratio": 28.5,
                "forward_pe": 25.0,
                "ps_ratio": 7.5,
                "pb_ratio": 40.2,
                "ev_ebitda": 22.3,
                "profit_margin": 0.26,
                "operating_margin": 0.31,
                "return_on_equity": 1.75,
                "revenue_ttm": 394328000000,
                "revenue_growth_yoy": 0.08,
                "eps_ttm": 6.42,
                "eps_growth_yoy": 0.12,
                "debt_to_equity": 176.3,
                "current_ratio": 0.94,
                "dividend_yield": 0.005,
                "beta": 1.24,
            }
        ]

        fetched, written = collector.store(session, data)

        assert fetched == 1
        assert written == 1
        session.execute.assert_called_once()
        session.flush.assert_called()

    def test_store_counts_correctly_with_multiple(self):
        """store() should count multiple records correctly."""
        collector = FundamentalsCollectorYF()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = [
            {"ticker": "AAPL", **{col: None for col in _UPSERT_COLUMNS}},
            {"ticker": "MSFT", **{col: None for col in _UPSERT_COLUMNS}},
            {"ticker": "GOOGL", **{col: None for col in _UPSERT_COLUMNS}},
        ]

        fetched, written = collector.store(session, data)

        assert fetched == 3
        assert written == 3
        assert session.execute.call_count == 3

    def test_store_empty_data(self):
        """store() with empty data should return zero counts."""
        collector = FundamentalsCollectorYF()
        session = MagicMock()

        fetched, written = collector.store(session, [])

        assert fetched == 0
        assert written == 0
        session.flush.assert_called()

    def test_store_with_missing_fields(self):
        """store() should handle records with missing optional fields."""
        collector = FundamentalsCollectorYF()
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute.return_value = mock_result

        data = [
            {
                "ticker": "AAPL",
                "market_cap": 2500000000000,
                # All other fields missing
            }
        ]

        fetched, written = collector.store(session, data)
        assert fetched == 1
        assert written == 1

    def test_upsert_columns_match_model(self):
        """All UPSERT columns should exist in the ORM model."""
        model_columns = {c.name for c in FundamentalsSnapshot.__table__.columns}
        for col in _UPSERT_COLUMNS:
            assert col in model_columns, f"Column {col} not found in model"
