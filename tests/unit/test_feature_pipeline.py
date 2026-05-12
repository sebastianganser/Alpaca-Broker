"""Tests for FeaturePipeline and TargetBackfillComputer."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from trading_signals.derived.feature_pipeline import FeaturePipeline


# ── Helper Mocks ─────────────────────────────────────────────────────────

class MockHolding:
    def __init__(self, ticker, weight_pct, etf_ticker="ARKK"):
        self.ticker = ticker
        self.weight_pct = weight_pct
        self.etf_ticker = etf_ticker
        self.shares = 1000
        self.snapshot_date = date(2026, 5, 1)


class MockInsiderTrade:
    def __init__(self, ticker, txn_type, txn_date, value=10000):
        self.ticker = ticker
        self.transaction_type = txn_type
        self.transaction_date = txn_date
        self.total_value = value
        self.is_derivative = False
        self.insider_name = "Test Insider"


class MockCluster:
    def __init__(self, ticker, start, end, score=5.0):
        self.ticker = ticker
        self.cluster_start = start
        self.cluster_end = end
        self.cluster_score = score
        self.n_insiders = 3
        self.n_buys = 5


class MockFundamentals:
    def __init__(self, ticker, pe=20.0, fwd_pe=18.0, ps=5.0,
                 rev_g=0.15, margin=0.12, dte=1.5):
        self.ticker = ticker
        self.pe_ratio = pe
        self.forward_pe = fwd_pe
        self.ps_ratio = ps
        self.revenue_growth_yoy = rev_g
        self.profit_margin = margin
        self.debt_to_equity = dte
        self.target_price_mean = 150.0
        self.snapshot_date = date(2026, 5, 1)


class MockTechnicalIndicator:
    def __init__(self, sma50=100, sma200=95, rsi=55, rs=1.05,
                 vol_sma=1000000, atr=2.5):
        self.sma_50 = sma50
        self.sma_200 = sma200
        self.rsi_14 = rsi
        self.relative_strength_spy = rs
        self.volume_sma_20 = vol_sma
        self.atr_14 = atr
        self.trade_date = date(2026, 5, 1)


# ── FeaturePipeline Tests ────────────────────────────────────────────────

class TestFeaturePipelineInit:
    """Test FeaturePipeline initialization."""

    def test_init(self):
        session = MagicMock()
        pipeline = FeaturePipeline(session)
        assert pipeline.session is session


class TestComputeTicker:
    """Test individual feature group computation."""

    def test_graceful_degradation(self):
        """If one feature group fails, others should still compute."""
        session = MagicMock()
        pipeline = FeaturePipeline(session)

        # Make all DB calls return None/empty to avoid real computation
        session.execute.return_value.scalar.return_value = None
        session.execute.return_value.scalars.return_value.all.return_value = []
        session.execute.return_value.all.return_value = []
        session.execute.return_value.first.return_value = None

        # Should not raise even if individual groups fail
        features = pipeline._compute_ticker("AAPL", date(2026, 5, 1))
        assert isinstance(features, dict)


class TestRatingScores:
    """Test the rating score mapping."""

    def test_rating_scores_mapped(self):
        from trading_signals.derived.feature_pipeline import _RATING_SCORES
        assert _RATING_SCORES["up"] == 1.0
        assert _RATING_SCORES["down"] == -1.0
        assert _RATING_SCORES["init"] == 0.0
        assert _RATING_SCORES["main"] == 0.5
        assert _RATING_SCORES["reit"] == 0.3


class TestUpsert:
    """Test UPSERT logic."""

    def test_upsert_skips_empty_features(self):
        """If all features are None, upsert should be skipped."""
        session = MagicMock()
        pipeline = FeaturePipeline(session)

        pipeline._upsert("AAPL", date(2026, 5, 1), {
            "ark_in_etf_count": None,
            "insider_net_buy_count_30d": None,
        })
        # No execute call should happen since all values are None
        session.execute.assert_not_called()

    def test_upsert_calls_execute_with_data(self):
        """If features have values, upsert should call execute."""
        session = MagicMock()
        pipeline = FeaturePipeline(session)

        pipeline._upsert("AAPL", date(2026, 5, 1), {
            "ark_in_etf_count": 3,
            "ark_total_weight": 5.5,
        })
        session.execute.assert_called_once()


# ── TargetBackfillComputer Tests ─────────────────────────────────────────

class TestTargetBackfill:
    """Test TargetBackfillComputer."""

    def test_init(self):
        from trading_signals.derived.target_backfill import TargetBackfillComputer
        session = MagicMock()
        computer = TargetBackfillComputer(session)
        assert computer.session is session

    def test_horizons_defined(self):
        from trading_signals.derived.target_backfill import HORIZONS
        assert len(HORIZONS) == 4
        names = [h[0] for h in HORIZONS]
        assert "return_1d" in names
        assert "return_5d" in names
        assert "return_20d" in names
        assert "return_60d" in names

    def test_backfill_with_no_missing(self):
        """If no rows have NULL returns, should return 0."""
        from trading_signals.derived.target_backfill import TargetBackfillComputer
        session = MagicMock()
        # All horizons: no missing rows
        session.execute.return_value.all.return_value = []

        computer = TargetBackfillComputer(session)
        result = computer.backfill_all()
        assert result == 0
