"""Unit tests for the rebuilt FeatureAnalysisEngine (no DB access)."""

from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from trading_signals.analysis import feature_report as fr
from trading_signals.analysis.feature_groups import (
    OTHER_GROUP,
    feature_group_of,
    group_features,
    is_market_wide,
)
from trading_signals.analysis.modeling import (
    prepare_model_frame,
    purged_lasso,
    walk_forward_rf,
)
from trading_signals.db.models.analysis import AnalysisReport
from trading_signals.db.models.features import (
    FEATURE_COLUMNS,
    KEY_COLUMNS,
    TARGET_COLUMNS,
)


def _synthetic(n_dates=120, n_tickers=40, seed=0):
    """Panel with one planted signal (price_vs_sma50) and noise features."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_dates)
    frames = []
    for d in dates:
        sig = rng.normal(size=n_tickers)
        market = rng.normal(0, 0.05)
        ret20 = market + 0.03 * sig + rng.normal(0, 0.03, n_tickers)
        multi = rng.random(n_tickers) < 0.3
        frames.append(pd.DataFrame({
            "snapshot_date": d.date(),
            "ticker": [f"T{i:02d}" for i in range(n_tickers)],
            "price_vs_sma50": sig,
            "rsi_14": rng.normal(50, 10, n_tickers),
            "pe_ratio": rng.normal(20, 5, n_tickers),
            "insider_cluster_score": rng.normal(size=n_tickers),
            "insider_buy_value_30d": rng.normal(size=n_tickers),
            "insider_cluster_active": rng.random(n_tickers) < 0.4,
            "consecutive_beats": rng.integers(0, 4, n_tickers),
            "ark_multi_etf_signal": pd.Series(multi, dtype=object).where(
                rng.random(n_tickers) > 0.1, None),
            "ark_conviction_score": rng.normal(size=n_tickers),
            "ark_weight_delta_20d": rng.normal(size=n_tickers),
            "analyst_downgrades_30d": rng.integers(0, 3, n_tickers),
            "analyst_upgrades_30d": rng.integers(0, 3, n_tickers),
            "cluster_count_60d": rng.integers(0, 3, n_tickers),
            "ark_conviction_streak": rng.integers(0, 6, n_tickers),
            "macro_vix": np.full(n_tickers, 15 + market * 10),  # market-wide
            "return_1d": market / 5 + rng.normal(0, 0.01, n_tickers),
            "return_5d": market / 2 + rng.normal(0, 0.02, n_tickers),
            "return_20d": ret20 + 0.02 * multi,
            "return_60d": rng.normal(0, 0.1, n_tickers),
        }))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def prepared():
    return fr.prepare_frame(_synthetic())


@pytest.fixture()
def engine(monkeypatch):
    # Synthetic frames have 60–120 dates; the independent-period guard
    # (10 × horizon) is tested separately below.
    monkeypatch.setattr(fr, "MIN_INDEPENDENT_PERIODS", 0)
    return fr.FeatureAnalysisEngine(MagicMock())


# ── groups / constants ───────────────────────────────────────────────


def test_feature_groups_cover_every_model_feature_exactly_once():
    flat = [f for fs in fr.FEATURE_GROUPS.values() for f in fs]
    assert sorted(flat) == sorted(FEATURE_COLUMNS)
    assert len(flat) == len(set(flat))
    assert fr.ALL_FEATURES == list(FEATURE_COLUMNS)
    assert fr.TARGET_RETURNS == list(TARGET_COLUMNS)
    assert not set(fr.ALL_FEATURES) & set(TARGET_COLUMNS)


def test_feature_group_rules():
    assert feature_group_of("eps_revision_pct_30d") == "Estimates"
    assert feature_group_of("pe_ratio") == "Fundamentals"
    assert feature_group_of("revenue_revision_pct_30d") == "Estimates"
    assert feature_group_of("news_volume_ratio_7d") == "Sentiment"
    assert feature_group_of("days_since_last_earnings") == "Earnings"
    assert feature_group_of("brand_new_feature") == OTHER_GROUP
    assert group_features(["x_new", "ark_total_weight"]) == {
        "ARK": ["ark_total_weight"], OTHER_GROUP: ["x_new"]}
    assert is_market_wide("macro_vix") and not is_market_wide("rsi_14")


# ── loading ──────────────────────────────────────────────────────────


def test_build_load_query_explicit_columns_and_param():
    table_cols = [*KEY_COLUMNS, "rsi_14", "return_20d", "computed_at",
                  "feature_version", "legacy_col"]
    sql, params = fr.build_load_query(table_cols, date(2021, 10, 1))
    s = str(sql)
    assert "*" not in s
    assert ":start" in s and params == {"start": date(2021, 10, 1)}
    assert '"rsi_14"' in s and '"return_20d"' in s
    assert "legacy_col" not in s and "computed_at" not in s
    assert "feature_version" not in s
    with pytest.raises(ValueError):
        fr.build_load_query(["rsi_14"], date(2021, 1, 1))


def test_load_data_uses_ml_start_date(monkeypatch, engine):
    captured = {}

    def fake_read_sql(sql, bind, params=None):
        captured["sql"], captured["params"] = str(sql), params
        return _synthetic(n_dates=3, n_tickers=5)

    monkeypatch.setattr(fr.pd, "read_sql", fake_read_sql)
    monkeypatch.setattr(fr, "ml_start_date", lambda: date(2022, 1, 1))
    engine.session.execute.return_value.scalars.return_value.all.return_value = [
        *KEY_COLUMNS, "rsi_14", "return_20d"]
    df = engine._load_data()
    assert captured["params"] == {"start": date(2022, 1, 1)}
    assert "WHERE snapshot_date >= :start" in captured["sql"]
    assert df.attrs.get("ts_prepared")
    assert pd.api.types.is_datetime64_any_dtype(df["snapshot_date"])


def test_prepare_frame_bool_and_excess(prepared):
    assert prepared["ark_multi_etf_signal"].dtype == np.float32
    assert set(prepared["ark_multi_etf_signal"].dropna().unique()) <= {0.0, 1.0}
    per_date_mean = prepared.groupby("snapshot_date")["return_20d"].mean()
    assert per_date_mean.abs().max() < 1e-9
    again = fr.prepare_frame(prepared)  # idempotent
    np.testing.assert_allclose(again["return_20d"], prepared["return_20d"], atol=1e-12)


# ── correlations ─────────────────────────────────────────────────────


def test_compute_correlations_daily_ic(engine, prepared):
    res = engine._compute_correlations(prepared)
    sig = res["price_vs_sma50"]["return_20d"]
    assert set(sig) >= {"rho", "pvalue", "significant", "icir", "t_nw", "n_dates"}
    assert sig["rho"] > 0.3 and sig["significant"] is True
    assert sig["n_dates"] == 120
    noise = res["rsi_14"]["return_20d"]
    assert abs(noise["rho"]) < 0.1
    # market-wide feature has no cross-sectional IC
    assert "macro_vix" not in res
    best = max(res, key=lambda f: abs(res[f].get("return_20d", {}).get("rho", 0)))
    assert best == "price_vs_sma50"


# ── models ───────────────────────────────────────────────────────────


def test_prepare_model_frame_ranks_and_drops_market_wide(prepared):
    feats = ["price_vs_sma50", "rsi_14", "macro_vix"]
    x, y, dates = prepare_model_frame(prepared, feats, "return_20d", max_rows=1000)
    assert list(x.columns) == ["price_vs_sma50", "rsi_14"]
    assert len(x) == 1000 and len(y) == 1000 and len(dates) == 1000
    assert x.min().min() >= 0.0 and x.max().max() <= 1.0
    assert np.all(np.diff(dates.astype("int64")) >= 0)


def test_rf_and_lasso_find_planted_signal(engine, prepared):
    rf = engine._compute_rf_importance(prepared)
    assert max(rf, key=lambda f: rf[f]["importance"]) == "price_vs_sma50"
    assert all(set(v) == {"importance", "std"} for v in rf.values())
    assert engine.diagnostics["rf_folds"]
    lasso = engine._compute_lasso_importance(prepared)
    assert max(lasso, key=lambda f: abs(lasso[f])) == "price_vs_sma50"
    assert lasso["price_vs_sma50"] > 0


def test_model_helpers_return_empty_without_valid_split(prepared):
    x, y, dates = prepare_model_frame(prepared, ["price_vs_sma50"], "return_20d")
    assert walk_forward_rf(x, y, dates, horizon=20, min_train_dates=10_000) == {}
    assert purged_lasso(x, y, dates, horizon=20, min_train_dates=10_000) == {}


# ── hypotheses ───────────────────────────────────────────────────────


def test_hypotheses_structure_and_h1_effect(engine, prepared):
    res = engine._test_hypotheses(prepared)
    for hid in ("H1", "H2", "H3", "H4", "H9", "H10", "H11", "H12", "H13"):
        assert hid in res, hid
        assert set(res[hid]) == {"verdict", "pvalue", "effect_size", "n_dates", "detail"}
        assert res[hid]["verdict"] in {"confirmed", "rejected", "inconclusive"}
    assert res["H1"]["verdict"] == "confirmed"
    assert res["H1"]["effect_size"] == pytest.approx(0.02, abs=0.01)


def test_short_history_is_never_significant(monkeypatch, prepared):
    """120 dates of 20d returns ≈ 6 independent periods → not reliable."""
    monkeypatch.setattr(fr, "MIN_INDEPENDENT_PERIODS", 10)
    eng = fr.FeatureAnalysisEngine(MagicMock())
    sig = eng._compute_correlations(prepared)["price_vs_sma50"]
    assert sig["return_20d"]["reliable"] is False
    assert sig["return_20d"]["significant"] is False
    assert sig["return_1d"]["reliable"] is True  # 120 ≥ 10 × 1
    hyp = eng._test_hypotheses(prepared)
    assert hyp["H1"]["verdict"] == "insufficient_data"


def test_min_dates_and_verdict():
    assert fr.min_dates_for_horizon(20) == 20 * fr.MIN_INDEPENDENT_PERIODS
    assert fr.min_dates_for_horizon(1) == max(fr.MIN_TEST_DATES, fr.MIN_INDEPENDENT_PERIODS)
    assert fr._verdict(0.001, 0.1, n_dates=44, horizon=20) == "insufficient_data"
    assert fr._verdict(0.001, 0.1, n_dates=400, horizon=20) == "confirmed"
    assert fr._verdict(0.001, -0.1) == "rejected"


def test_consensus_ignores_unreliable_ic(engine):
    results = {
        "feature_correlations": {
            "a": {"return_20d": {"rho": 0.5, "reliable": False}},
            "b": {"return_20d": {"rho": 0.1, "reliable": True}},
        },
        "feature_importance_rf": {},
        "feature_importance_lasso": {},
    }
    ranks = {c["feature"]: c["spearman_rank"] for c in engine._build_consensus(results)}
    assert ranks["b"] == 1 and ranks["a"] == 2


def test_h10_uses_zero_beats_not_negative(engine):
    df = _synthetic(n_dates=60, n_tickers=40, seed=3)
    rng = np.random.default_rng(1)
    df["consecutive_beats"] = rng.integers(0, 3, len(df))
    df["insider_cluster_active"] = rng.random(len(df)) < 0.5
    boost = (df["consecutive_beats"] == 0) & df["insider_cluster_active"]
    df["return_20d"] = df["return_20d"] + 0.05 * boost
    res = engine._test_hypotheses(fr.prepare_frame(df))
    assert res["H10"]["verdict"] == "confirmed"
    assert res["H10"]["effect_size"] > 0.03


# ── end-to-end ───────────────────────────────────────────────────────


def test_run_end_to_end_returns_transient_report(monkeypatch, engine):
    df = fr.prepare_frame(_synthetic(n_dates=80, n_tickers=30))
    monkeypatch.setattr(engine, "_load_data", lambda: df)
    captured = {}

    def fake_store(d_start, d_end, snaps, tickers, results, html, comp_time):
        captured.update(results=results, html=html)
        raise RuntimeError("no db in unit tests")

    monkeypatch.setattr(engine, "_store_results", fake_store)
    report = engine.run()
    assert isinstance(report, AnalysisReport)
    assert report.snapshot_count == len(df) and report.ticker_count == 30
    res = captured["results"]
    assert res["feature_correlations"] and res["consensus_features"]
    assert set(res["consensus_features"][0]) == {
        "feature", "spearman_rank", "rf_rank", "lasso_rank", "avg_rank"}
    assert "Mean daily rank IC" in captured["html"]
    # JSON-safe: no NaN anywhere in correlation payload
    for tgts in res["feature_correlations"].values():
        for v in tgts.values():
            for val in v.values():
                assert not (isinstance(val, float) and np.isnan(val))


def test_run_aborts_with_too_few_dates(monkeypatch, engine):
    monkeypatch.setattr(engine, "_load_data",
                        lambda: fr.prepare_frame(_synthetic(n_dates=10, n_tickers=5)))
    assert engine.run() is None
