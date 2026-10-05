"""Tests for IndexSyncer."""

from unittest.mock import MagicMock, patch

import pytest

from trading_signals.universe.index_sync import (
    IndexSyncer,
    IndexSyncSanityError,
    SyncResult,
    check_index_sizes,
)


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

            result = syncer.sync(dry_run=True, sanity_check=False)

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
                result = syncer.sync(dry_run=False, sanity_check=False)

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

            result = syncer.sync(dry_run=False, sanity_check=False)

        assert result.newly_added == 0
        assert result.not_tradeable == 1


class TestIndexSyncSanity:
    """H2: implausible list sizes must abort before any DB update."""

    @patch.object(IndexSyncer, "_fetch_nasdaq100", return_value=set())
    @patch.object(
        IndexSyncer, "_fetch_sp500", return_value={f"T{i}" for i in range(503)}
    )
    def test_empty_nasdaq_table_aborts_before_updates(self, mock_sp, mock_nq):
        session = MagicMock()
        syncer = IndexSyncer(session)
        with pytest.raises(IndexSyncSanityError):
            syncer.sync(dry_run=False)
        session.execute.assert_not_called()
        session.add.assert_not_called()

    def test_plausible_sizes_pass(self):
        check_index_sizes({
            "sp500": {f"S{i}" for i in range(503)},
            "nasdaq100": {f"N{i}" for i in range(101)},
        })

    @patch.object(
        IndexSyncer, "_fetch_nasdaq100", return_value={f"N{i}" for i in range(101)}
    )
    @patch.object(
        IndexSyncer, "_fetch_sp500", return_value={f"S{i}" for i in range(503)}
    )
    def test_leavers_membership_cleared(self, mock_sp, mock_nq):
        session = MagicMock()
        existing = [("S1", ["sp500"]), ("GONE", ["sp500"]), ("NEVER", None)]
        session.execute.return_value.all.side_effect = [existing, [], []]
        syncer = IndexSyncer(session)
        with patch(
            "trading_signals.universe.index_sync.AlpacaAssetValidator"
        ) as mock_validator, patch.object(syncer._manager, "add_ticker"):
            mock_validator.return_value.fetch_all_assets.return_value = {}
            result = syncer.sync(dry_run=False)
        assert result.membership_cleared == 1
        cleared = [
            c[0][0] for c in session.execute.call_args_list
            if "index_membership" in str(c[0][0])
            and "GONE" in str(c[0][0].compile(compile_kwargs={"literal_binds": True}))
        ]
        assert cleared, "GONE must have its index_membership cleared"
