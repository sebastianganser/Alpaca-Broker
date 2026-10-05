"""Point-in-time / pure-helper tests for the feature pipeline (review 2026-10)."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from trading_signals.db.models.features import FEATURE_COLUMNS
from trading_signals.derived import feature_pipeline as fp
from trading_signals.derived.feature_pipeline import (
    FEATURE_VERSION,
    FeaturePipeline,
    ark_state_at,
    build_upsert_values,
    compute_ark_features,
    earnings_history_features,
    insider_cluster_features,
    insider_trade_features,
    liquidity_from_prices,
    macro_derived,
    news_cutoff_utc,
)


def _sessions(d: date, n: int) -> list[date]:
    """n weekday dates ending at d, newest first."""
    out, cur = [], d
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur -= timedelta(days=1)
    return out


# ── compute_daily (H3) ──────────────────────────────────────────────


class TestComputeDailyCalendar:
    def test_non_trading_day_writes_nothing(self):
        session = MagicMock()
        pipeline = FeaturePipeline(session)
        with patch.object(fp, "is_trading_day", return_value=False):
            assert pipeline.compute_daily(date(2026, 5, 2)) == 0
        session.execute.assert_not_called()

    def test_caches_reset_per_run(self):
        session = MagicMock()
        pipeline = FeaturePipeline(session)
        pipeline._macro_cache["x"] = 1
        with patch.object(fp, "is_trading_day", return_value=False):
            pipeline.compute_daily(date(2026, 5, 2))
        assert pipeline._macro_cache == {}

    def test_trading_day_counts_written_rows(self):
        session = MagicMock()
        pipeline = FeaturePipeline(session)
        with (
            patch.object(fp, "is_trading_day", return_value=True),
            patch.object(pipeline, "_get_active_tickers", return_value=["A", "B"]),
            patch.object(pipeline, "_compute_ticker", return_value={}),
            patch.object(pipeline, "_upsert", side_effect=[True, False]),
        ):
            assert pipeline.compute_daily(date(2026, 5, 1)) == 1


# ── UPSERT row (H1) ─────────────────────────────────────────────────


class TestBuildUpsertValues:
    def test_contains_every_feature_column_and_version(self):
        values = build_upsert_values("AAPL", date(2026, 5, 1), {"rsi_14": 50.0})
        for col in FEATURE_COLUMNS:
            assert col in values
        assert values["rsi_14"] == 50.0
        assert values["pe_ratio"] is None
        assert values["feature_version"] == FEATURE_VERSION
        assert values["ticker"] == "AAPL"

    def test_unknown_keys_dropped(self):
        values = build_upsert_values("AAPL", date(2026, 5, 1), {"bogus": 1})
        assert "bogus" not in values


# ── News cutoff (C4) ────────────────────────────────────────────────


class TestNewsCutoff:
    def test_summer_edt(self):
        cutoff = news_cutoff_utc(date(2026, 7, 1))
        assert cutoff.tzinfo is not None
        assert cutoff == datetime(2026, 7, 1, 20, 0, tzinfo=UTC)

    def test_winter_est(self):
        assert news_cutoff_utc(date(2026, 1, 15)) == datetime(2026, 1, 15, 21, 0, tzinfo=UTC)


# ── ARK (H2 / M6) ───────────────────────────────────────────────────


class TestArkStateAt:
    def test_exit_drops_ticker(self):
        rows = [
            (date(2026, 5, 1), "ARKK", "TSLA", 5.0, 100),
            (date(2026, 5, 1), "ARKK", "ROKU", 2.0, 50),
            (date(2026, 5, 4), "ARKK", "ROKU", 2.0, 50),
        ]
        assert "TSLA" in ark_state_at(rows, date(2026, 5, 1))
        state = ark_state_at(rows, date(2026, 5, 4))
        assert "TSLA" not in state
        assert state["ROKU"]["ARKK"] == (2.0, 50.0)

    def test_future_snapshot_ignored(self):
        rows = [(date(2026, 5, 5), "ARKK", "TSLA", 5.0, 100)]
        assert ark_state_at(rows, date(2026, 5, 4)) == {}

    def test_stale_snapshot_ignored(self):
        rows = [(date(2026, 4, 1), "ARKK", "TSLA", 5.0, 100)]
        assert ark_state_at(rows, date(2026, 5, 1)) == {}

    def test_per_etf_latest(self):
        rows = [
            (date(2026, 5, 1), "ARKK", "TSLA", 5.0, 100),
            (date(2026, 4, 30), "ARKW", "TSLA", 3.0, 10),
        ]
        state = ark_state_at(rows, date(2026, 5, 1))
        assert set(state["TSLA"]) == {"ARKK", "ARKW"}


class TestComputeArkFeatures:
    def test_not_held_returns_empty(self):
        dates = _sessions(date(2026, 5, 29), 21)
        assert compute_ark_features("TSLA", [{}] * 21, dates) == {}

    def test_new_position_positive_delta(self):
        dates = _sessions(date(2026, 5, 29), 21)
        states = [{"TSLA": {"ARKK": (5.0, 100.0)}}] + [{}] * 20
        f = compute_ark_features("TSLA", states, dates)
        assert f["ark_in_etf_count"] == 1
        assert f["ark_weight_delta_1d"] == pytest.approx(5.0)
        assert f["ark_weight_delta_20d"] == pytest.approx(5.0)

    def test_exited_recently_still_has_features(self):
        dates = _sessions(date(2026, 5, 29), 21)
        states = [{}] + [{"TSLA": {"ARKK": (4.0, 100.0)}}] * 20
        f = compute_ark_features("TSLA", states, dates)
        assert f["ark_in_etf_count"] == 0
        assert f["ark_total_weight"] == 0.0
        assert f["ark_weight_delta_1d"] == pytest.approx(-4.0)
        assert f["ark_conviction_streak"] == 0

    def test_increase_days_count_sessions_not_etfs(self):
        dates = _sessions(date(2026, 5, 29), 21)
        # Two ETFs both increase on the same session -> one increase day
        states = [
            {"TSLA": {"ARKK": (5.0, 200.0), "ARKW": (3.0, 200.0)}},
            {"TSLA": {"ARKK": (5.0, 100.0), "ARKW": (3.0, 100.0)}},
        ] + [{"TSLA": {"ARKK": (5.0, 100.0), "ARKW": (3.0, 100.0)}}] * 19
        f = compute_ark_features("TSLA", states, dates)
        assert f["ark_increase_days_10d"] == 1
        assert f["ark_conviction_streak"] == 1
        assert f["ark_multi_etf_signal"] is True

    def test_streak_skips_unchanged_and_stops_at_decrease(self):
        dates = _sessions(date(2026, 5, 29), 21)
        h = lambda s: {"TSLA": {"ARKK": (5.0, float(s))}}  # noqa: E731
        # newest first: 300 (up) 200 (unchanged) 200 (up) 100 (down) 150 ...
        states = [h(300), h(200), h(200), h(100), h(150)] + [h(150)] * 16
        f = compute_ark_features("TSLA", states, dates)
        assert f["ark_conviction_streak"] == 2


# ── Insider (C2) ────────────────────────────────────────────────────


def _trade(name, txn, filed, value=50_000):
    return SimpleNamespace(
        insider_name=name, transaction_date=txn, filing_date=filed, total_value=value
    )


class TestInsiderClusterFeatures:
    def test_cluster_counts(self):
        d = date(2026, 5, 20)
        purchases = [
            _trade("A", date(2026, 5, 1), date(2026, 5, 3)),
            _trade("B", date(2026, 5, 5), date(2026, 5, 7)),
        ]
        f = insider_cluster_features(purchases, d)
        assert f["insider_cluster_active"] is True
        assert f["cluster_count_30d"] == 1
        assert f["days_since_last_cluster"] == 15
        assert f["insider_cluster_score"] > 0

    def test_no_cluster_gives_zero_counts(self):
        d = date(2026, 5, 20)
        f = insider_cluster_features([_trade("A", date(2026, 5, 1), None)], d)
        assert f["insider_cluster_active"] is False
        assert f["cluster_count_30d"] == 0
        assert f["cluster_score_sum_60d"] == 0
        assert f["days_since_last_cluster"] is None

    def test_old_cluster_not_active(self):
        d = date(2026, 5, 20)
        purchases = [
            _trade("A", date(2026, 3, 1), date(2026, 3, 3)),
            _trade("B", date(2026, 3, 2), date(2026, 3, 4)),
        ]
        f = insider_cluster_features(purchases, d)
        assert f["insider_cluster_active"] is False
        assert f["insider_cluster_score"] is None
        assert f["cluster_count_60d"] == 0


class TestInsiderTradeFeatures:
    def test_filing_date_window(self):
        d = date(2026, 5, 20)
        trades = [
            ("P", date(2026, 5, 10), 1000.0),
            ("P", date(2026, 5, 21), 9999.0),  # filed after d -> ignored
            ("S", date(2026, 5, 15), 3000.0),
            ("P", date(2026, 3, 1), 500.0),  # outside 30d, inside 90d
        ]
        f = insider_trade_features(trades, d)
        assert f["insider_net_buy_count_30d"] == 0
        assert f["insider_buy_value_30d"] == 1000.0
        assert f["insider_buy_ratio_30d"] == pytest.approx(0.25)
        assert f["insider_buy_ratio_90d"] == pytest.approx(1500 / 4500, abs=1e-4)

    def test_empty_counts_zero(self):
        f = insider_trade_features([], date(2026, 5, 20))
        assert f["insider_net_buy_count_30d"] == 0
        assert f["insider_buy_ratio_30d"] is None


# ── Liquidity / earnings / macro ────────────────────────────────────


class TestLiquidity:
    def test_too_few_rows(self):
        assert liquidity_from_prices([(10.0, 100)] * 4) == {}

    def test_values(self):
        rows = [(10.0, 100), (11.0, 100), (11.0, 100), (10.0, 100), (10.0, 100)]
        f = liquidity_from_prices(rows)
        assert f["dollar_volume_20d"] == pytest.approx(1040.0)
        assert f["amihud_illiquidity_20d"] > 0


class TestEarningsHistory:
    def test_no_history_is_null(self):
        f = earnings_history_features([], date(2026, 5, 20))
        assert f["consecutive_beats"] is None

    def test_zero_beats_is_zero(self):
        past = [(date(2026, 4, 20), 1.0, 0.9, -10.0)]
        f = earnings_history_features(past, date(2026, 5, 20))
        assert f["consecutive_beats"] == 0
        assert f["days_since_last_earnings"] == 30

    def test_beat_streak(self):
        past = [
            (date(2026, 4, 20), 1.0, 1.2, 20.0),
            (date(2026, 1, 20), 1.0, 1.1, 10.0),
            (date(2025, 10, 20), 1.0, 0.9, -10.0),
        ]
        f = earnings_history_features(past, date(2026, 5, 20))
        assert f["consecutive_beats"] == 2
        assert f["surprise_trend_3q"] == pytest.approx(20 / 3, abs=1e-4)


class TestMacroDerived:
    def test_values(self):
        f = macro_derived({"DGS2": 4.0, "DGS10": 3.5, "VIXCLS": 20.0})
        assert f["macro_yield_spread"] == pytest.approx(-0.5)
        assert f["macro_vix_regime"] == 1
        assert f["macro_dollar_index"] is None

    def test_zero_kept(self):
        f = macro_derived({"BAMLH0A0HYM2": 0.0})
        assert f["macro_hy_spread"] == 0.0
