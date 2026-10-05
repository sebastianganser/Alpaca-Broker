"""Tests for Features API endpoints.

Tests the /features/ endpoints, the model-derived feature groups and
their schemas. Uses mock rows to avoid requiring a real database.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from trading_signals.api.routes.features import (
    ALL_FEATURE_COLS,
    FEATURE_GROUP_DEFS,
    FEATURE_GROUPS,
    MARKET_WIDE_GROUPS,
    SIGNAL_GROUPS,
    SOURCE_INDICATORS,
    _count_filled,
    active_sources,
    build_feature_groups,
    group_key,
    group_meta,
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
from trading_signals.db.models.features import (
    FEATURE_COLUMNS,
    META_COLUMNS,
    TARGET_COLUMNS,
    FeatureSnapshot,
)

# ── Group definitions vs. model (model is the source of truth) ───────────


class TestFeatureGroupDefinitions:
    """Verify the API feature groups are derived from the model."""

    def test_groups_cover_exactly_the_model_feature_columns(self):
        assert sorted(ALL_FEATURE_COLS) == sorted(FEATURE_COLUMNS)

    def test_no_duplicates(self):
        assert len(ALL_FEATURE_COLS) == len(set(ALL_FEATURE_COLS))

    def test_meta_and_target_columns_excluded(self):
        excluded = set(META_COLUMNS) | set(TARGET_COLUMNS) | {"snapshot_date", "ticker"}
        assert not excluded & set(ALL_FEATURE_COLS)

    def test_group_totals_match_model_columns(self):
        """Per-group totals are computed from the model, never hard-coded."""
        model_cols = {c.name for c in FeatureSnapshot.__table__.columns}
        for g in FEATURE_GROUP_DEFS:
            assert set(g.columns) <= model_cols
        meta = {m.key: m.total for m in group_meta()}
        assert meta == {g.key: len(g.columns) for g in FEATURE_GROUP_DEFS}
        assert sum(meta.values()) == len(FEATURE_COLUMNS)

    def test_expected_groups_present(self):
        expected = {
            "ARK",
            "Insider",
            "Analyst",
            "Politician",
            "13F",
            "Fundamentals",
            "Technical",
            "Earnings",
            "Sentiment",
            "Liquidity",
            "Macro",
            "Breadth",
            "Sector",
            "Short Interest",
            "Options IV",
            "Estimates",
        }
        assert expected <= set(FEATURE_GROUPS)

    def test_group_keys_are_unique_and_stable(self):
        keys = [g.key for g in FEATURE_GROUP_DEFS]
        assert len(keys) == len(set(keys))
        assert group_key("13F") == "form13f"
        assert group_key("Short Interest") == "short_interest"
        assert group_key("ARK") == "ark"

    def test_new_model_column_lands_in_a_group(self):
        groups = build_feature_groups([*FEATURE_COLUMNS, "brand_new_feature"])
        cols = [c for g in groups for c in g.columns]
        assert "brand_new_feature" in cols
        assert groups[-1].key == "other"

    def test_source_indicators_are_valid_columns(self):
        for group, col in SOURCE_INDICATORS.items():
            assert col in FEATURE_GROUPS[group], f"Invalid column {col} for {group}"

    def test_market_wide_groups(self):
        labels = {g.label for g in MARKET_WIDE_GROUPS}
        assert labels == {"Macro", "Breadth"}

    def test_signal_groups_exclude_market_wide(self):
        labels = {g.label for g in SIGNAL_GROUPS}
        assert "Macro" not in labels
        assert "Breadth" not in labels
        assert {"ARK", "Insider", "Analyst", "Sentiment"} <= labels


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
        assert (
            _count_filled(row, ["ark_multi_etf_signal", "insider_cluster_active"]) == 2
        )

    def test_zero_values_count(self):
        """Zero is a valid value, should count as filled."""
        row = MagicMock()
        row.ark_in_etf_count = 0
        assert _count_filled(row, ["ark_in_etf_count"]) == 1


class TestActiveSources:
    """Convergence source detection."""

    @staticmethod
    def _row(**values):
        base = dict.fromkeys(ALL_FEATURE_COLS)
        base.update(values)
        return SimpleNamespace(**base)

    def test_no_values_no_sources(self):
        assert active_sources(self._row()) == []

    def test_count_indicator_requires_positive(self):
        assert active_sources(self._row(ark_in_etf_count=0)) == []
        assert active_sources(self._row(ark_in_etf_count=2)) == ["ARK"]

    def test_score_indicator_counts_when_set(self):
        assert active_sources(self._row(analyst_rating_score=Decimal("0"))) == [
            "Analyst"
        ]

    def test_market_wide_never_counts(self):
        row = self._row(
            macro_vix=Decimal("18.2"), breadth_advance_decline=Decimal("0.5")
        )
        assert active_sources(row) == []

    def test_max_sources_bounds_active_sources(self):
        row = self._row(**{g.indicator: 1 for g in FEATURE_GROUP_DEFS if g.indicator})
        assert len(active_sources(row)) == len(SIGNAL_GROUPS)


# ── Schema Validation Tests ──────────────────────────────────────────────


class TestCoverageSchemas:
    """Test Pydantic schemas for feature coverage."""

    def test_coverage_item_defaults(self):
        item = FeatureCoverageItem(ticker="AAPL")
        assert item.ticker == "AAPL"
        assert item.counts == {}
        assert item.total_filled == 0
        assert item.total_possible == 0

    def test_coverage_item_with_values(self):
        item = FeatureCoverageItem(
            ticker="TSLA",
            counts={"ark": 11, "insider": 4, "short_interest": 3},
            total_filled=18,
            total_possible=len(FEATURE_COLUMNS),
        )
        assert item.counts["ark"] == 11
        assert item.total_filled == 18

    def test_coverage_response_empty(self):
        resp = FeatureCoverageResponse()
        assert resp.snapshot_date is None
        assert resp.items == []
        assert resp.groups == []
        assert resp.ticker_count == 0

    def test_coverage_response_with_groups(self):
        resp = FeatureCoverageResponse(
            groups=group_meta(), total_possible=len(ALL_FEATURE_COLS)
        )
        assert {g.key for g in resp.groups} == {g.key for g in FEATURE_GROUP_DEFS}
        assert resp.total_possible == len(FEATURE_COLUMNS)


class TestConvergenceSchemas:
    """Test Pydantic schemas for signal convergence."""

    def test_convergence_item(self):
        item = SignalConvergenceItem(
            ticker="AMZN",
            active_sources=6,
            source_names=[
                "ARK",
                "Analyst",
                "Politician",
                "Fundamentals",
                "Technical",
                "Earnings",
            ],
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
        assert resp.max_sources == 0


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
                HorizonStats(
                    horizon="1d", filled_count=0, total_count=674, filled_pct=0.0
                ),
                HorizonStats(
                    horizon="5d", filled_count=0, total_count=674, filled_pct=0.0
                ),
            ],
        )
        assert len(resp.horizons) == 2
        assert resp.total_snapshots == 674


class TestTickerDetailSchemas:
    """Test Pydantic schemas for ticker feature detail."""

    def test_feature_group_detail(self):
        g = FeatureGroupDetail(
            group="ARK",
            key="ark",
            features={"ark_in_etf_count": 3, "ark_total_weight": 5.5},
            filled=2,
            total=11,
        )
        assert g.group == "ARK"
        assert g.filled == 2

    def test_feature_group_detail_keeps_bool(self):
        g = FeatureGroupDetail(group="ARK", features={"ark_multi_etf_signal": True})
        assert g.features["ark_multi_etf_signal"] is True

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
