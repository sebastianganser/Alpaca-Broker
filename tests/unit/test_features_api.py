"""Tests for Features API endpoints.

Tests the 4 new /features/ endpoints and their schemas.
Uses mock DB sessions to avoid requiring a real database.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from trading_signals.api.routes.features import (
    FEATURE_GROUPS,
    ALL_FEATURE_COLS,
    SOURCE_INDICATORS,
    _get_latest_date,
    _count_filled,
)
from trading_signals.api.schemas import (
    FeatureCoverageItem,
    FeatureCoverageResponse,
    FeatureGroupDetail,
    HorizonStats,
    ReturnStatsResponse,
    SignalConvergenceItem,
    SignalConvergenceResponse,
    TickerFeatureDetail,
)


# ── Schema / Constants Tests ─────────────────────────────────────────────

class TestFeatureGroupDefinitions:
    """Verify feature group structure is consistent with the model."""

    def test_all_groups_present(self):
        """All 9 signal groups must be defined."""
        expected_groups = {"ARK", "Insider", "Analyst", "Politician", "13F",
                          "Fundamentals", "Technical", "Earnings", "Sentiment"}
        assert set(FEATURE_GROUPS.keys()) == expected_groups

    def test_ark_has_11_features(self):
        assert len(FEATURE_GROUPS["ARK"]) == 11

    def test_insider_has_8_features(self):
        assert len(FEATURE_GROUPS["Insider"]) == 8

    def test_analyst_has_7_features(self):
        assert len(FEATURE_GROUPS["Analyst"]) == 7

    def test_politician_has_4_features(self):
        assert len(FEATURE_GROUPS["Politician"]) == 4

    def test_13f_has_2_features(self):
        assert len(FEATURE_GROUPS["13F"]) == 2

    def test_fundamentals_has_8_features(self):
        assert len(FEATURE_GROUPS["Fundamentals"]) == 8

    def test_technical_has_6_features(self):
        assert len(FEATURE_GROUPS["Technical"]) == 6

    def test_earnings_has_3_features(self):
        assert len(FEATURE_GROUPS["Earnings"]) == 3

    def test_total_feature_count(self):
        """Total features across all groups should be 55 (49 base + 6 sentiment)."""
        assert len(ALL_FEATURE_COLS) == 55

    def test_no_duplicates(self):
        """Each feature column name should be unique."""
        assert len(ALL_FEATURE_COLS) == len(set(ALL_FEATURE_COLS))

    def test_source_indicators_cover_all_groups(self):
        """Every group must have a source indicator defined."""
        for group in FEATURE_GROUPS:
            assert group in SOURCE_INDICATORS, f"Missing source indicator for {group}"

    def test_source_indicators_are_valid_columns(self):
        """Each source indicator must reference a valid feature column."""
        for group, col in SOURCE_INDICATORS.items():
            assert col in ALL_FEATURE_COLS, f"Invalid column {col} for {group}"


# ── Helper Function Tests ────────────────────────────────────────────────

class TestCountFilled:
    """Test the _count_filled helper function."""

    def test_all_none(self):
        row = MagicMock()
        row.ark_in_etf_count = None
        row.ark_total_weight = None
        assert _count_filled(row, ["ark_in_etf_count", "ark_total_weight"]) == 0

    def test_all_filled(self):
        row = MagicMock()
        row.ark_in_etf_count = 3
        row.ark_total_weight = Decimal("5.5")
        assert _count_filled(row, ["ark_in_etf_count", "ark_total_weight"]) == 2

    def test_partial_filled(self):
        row = MagicMock()
        row.rsi_14 = Decimal("55.0")
        row.price_vs_sma50 = None
        row.price_vs_sma200 = Decimal("0.12")
        assert _count_filled(row, ["rsi_14", "price_vs_sma50", "price_vs_sma200"]) == 2

    def test_boolean_values_count(self):
        row = MagicMock()
        row.ark_multi_etf_signal = True
        row.insider_cluster_active = False  # False is not None!
        assert _count_filled(row, ["ark_multi_etf_signal", "insider_cluster_active"]) == 2

    def test_zero_values_count(self):
        """Zero is a valid value, should count as filled."""
        row = MagicMock()
        row.ark_in_etf_count = 0
        assert _count_filled(row, ["ark_in_etf_count"]) == 1


# ── Schema Validation Tests ──────────────────────────────────────────────

class TestCoverageSchemas:
    """Test Pydantic schemas for feature coverage."""

    def test_coverage_item_defaults(self):
        item = FeatureCoverageItem(ticker="AAPL")
        assert item.ticker == "AAPL"
        assert item.ark == 0
        assert item.total_filled == 0
        assert item.total_possible == 55  # 49 base + 6 sentiment (Sprint 8c)

    def test_coverage_item_with_values(self):
        item = FeatureCoverageItem(
            ticker="TSLA",
            ark=11, insider=4, analyst=6, politician=2,
            form13f=1, fundamentals=8, technical=6, earnings=3,
            total_filled=41,
        )
        assert item.total_filled == 41

    def test_coverage_response_empty(self):
        resp = FeatureCoverageResponse()
        assert resp.snapshot_date is None
        assert resp.items == []
        assert resp.ticker_count == 0


class TestConvergenceSchemas:
    """Test Pydantic schemas for signal convergence."""

    def test_convergence_item(self):
        item = SignalConvergenceItem(
            ticker="AMZN",
            active_sources=6,
            source_names=["ARK", "Analyst", "Politician", "Fundamentals", "Technical", "Earnings"],
        )
        assert item.active_sources == 6
        assert len(item.source_names) == 6

    def test_convergence_item_with_scores(self):
        item = SignalConvergenceItem(
            ticker="AAPL",
            active_sources=3,
            source_names=["Analyst", "Fundamentals", "Technical"],
            analyst_rating_score=0.5,
            rsi_14=73.3,
        )
        assert item.ark_conviction_score is None
        assert item.analyst_rating_score == 0.5

    def test_convergence_response_empty(self):
        resp = SignalConvergenceResponse()
        assert resp.snapshot_date is None
        assert resp.items == []


class TestReturnSchemas:
    """Test Pydantic schemas for return statistics."""

    def test_horizon_stats(self):
        h = HorizonStats(
            horizon="1d",
            filled_count=500,
            total_count=674,
            filled_pct=74.2,
            mean=0.001234,
            median=0.000890,
            std=0.023456,
        )
        assert h.horizon == "1d"
        assert h.min_val is None
        assert h.max_val is None

    def test_return_stats_response(self):
        resp = ReturnStatsResponse(
            total_snapshots=674,
            horizons=[
                HorizonStats(horizon="1d", filled_count=0, total_count=674, filled_pct=0.0),
                HorizonStats(horizon="5d", filled_count=0, total_count=674, filled_pct=0.0),
            ],
        )
        assert len(resp.horizons) == 2
        assert resp.total_snapshots == 674


class TestTickerDetailSchemas:
    """Test Pydantic schemas for ticker feature detail."""

    def test_feature_group_detail(self):
        g = FeatureGroupDetail(
            group="ARK",
            features={"ark_in_etf_count": 3, "ark_total_weight": 5.5},
            filled=2,
            total=11,
        )
        assert g.group == "ARK"
        assert g.filled == 2

    def test_ticker_feature_detail(self):
        detail = TickerFeatureDetail(
            ticker="AAPL",
            snapshot_date=date(2026, 5, 12),
            groups=[
                FeatureGroupDetail(group="ARK", features={}, filled=0, total=11),
            ],
            total_filled=28,
            return_1d=0.0123,
        )
        assert detail.ticker == "AAPL"
        assert detail.total_filled == 28
        assert detail.return_5d is None

    def test_ticker_detail_all_returns_null(self):
        detail = TickerFeatureDetail(ticker="NODATA")
        assert detail.return_1d is None
        assert detail.return_5d is None
        assert detail.return_20d is None
        assert detail.return_60d is None
        assert detail.total_filled == 0
