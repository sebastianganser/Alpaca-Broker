"""Tests for IndexSyncer."""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from trading_signals.universe.index_sync import IndexSyncer, SyncResult


class TestSyncResult:
    """Test the SyncResult dataclass."""

    def test_default_values(self):
        result = SyncResult()
        assert result.sp500_count == 0
        assert result.nasdaq100_count == 0
        assert result.newly_added == 0
        assert result.new_tickers == []

    def test_fields_independent(self):
        r1 = SyncResult()
        r2 = SyncResult()
        r1.new_tickers.append("TEST")
        assert r2.new_tickers == []


class TestIndexSyncer:
    """Test IndexSyncer with mocked API calls."""

    @patch.object(IndexSyncer, "_fetch_nasdaq100", return_value={"AAPL", "MSFT"})
    @patch.object(IndexSyncer, "_fetch_sp500", return_value={"AAPL", "GOOG", "AMZN"})
    def test_sync_dry_run(self, mock_sp, mock_nq):
        """Dry run should not modify anything."""
        session = MagicMock()
        # Mock existing universe as empty
        session.execute.return_value.all.return_value = []

        syncer = IndexSyncer(session)

        with patch(
            "trading_signals.universe.index_sync.AlpacaAssetValidator"
        ) as mock_validator:
            mock_instance = MagicMock()
            mock_instance.fetch_all_assets.return_value = {
                "AAPL": MagicMock(tradable=True, exchange="NASDAQ", name="Apple"),
                "MSFT": MagicMock(tradable=True, exchange="NASDAQ", name="Microsoft"),
                "GOOG": MagicMock(tradable=True, exchange="NASDAQ", name="Alphabet"),
                "AMZN": MagicMock(tradable=True, exchange="NASDAQ", name="Amazon"),
            }
            mock_validator.return_value = mock_instance

            result = syncer.sync(dry_run=True)

        assert result.sp500_count == 3
        assert result.nasdaq100_count == 2

    @patch.object(IndexSyncer, "_fetch_nasdaq100", return_value=set())
    @patch.object(IndexSyncer, "_fetch_sp500", return_value={"NEW_TICKER"})
    def test_sync_adds_new_tickers(self, mock_sp, mock_nq):
        """New tickers should be added if tradeable on Alpaca."""
        session = MagicMock()
        # Empty universe
        session.execute.return_value.all.return_value = []

        syncer = IndexSyncer(session)

        with patch(
            "trading_signals.universe.index_sync.AlpacaAssetValidator"
        ) as mock_validator:
            mock_instance = MagicMock()
            mock_instance.fetch_all_assets.return_value = {
                "NEW_TICKER": MagicMock(
                    tradable=True, exchange="NYSE", name="New Corp"
                ),
            }
            mock_validator.return_value = mock_instance

            with patch.object(syncer._manager, "add_ticker"):
                result = syncer.sync(dry_run=False)

        assert result.newly_added == 1
        assert "NEW_TICKER" in result.new_tickers

    @patch.object(IndexSyncer, "_fetch_nasdaq100", return_value=set())
    @patch.object(IndexSyncer, "_fetch_sp500", return_value={"FAKE_TICKER"})
    def test_sync_skips_untradeable(self, mock_sp, mock_nq):
        """Untradeable tickers should not be added."""
        session = MagicMock()
        session.execute.return_value.all.return_value = []

        syncer = IndexSyncer(session)

        with patch(
            "trading_signals.universe.index_sync.AlpacaAssetValidator"
        ) as mock_validator:
            mock_instance = MagicMock()
            mock_instance.fetch_all_assets.return_value = {}  # No assets match
            mock_validator.return_value = mock_instance

            result = syncer.sync(dry_run=False)

        assert result.newly_added == 0
        assert result.not_tradeable == 1
