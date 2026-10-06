"""Tests for the walk-forward hit-probability model (concept §7.2, step B)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_signals.analysis.hit_model import (
    BREAK_EVEN_HIT_RATE,
    PURGE_SESSIONS,
    STOP_NET,
    Rule,
    build_design,
    choose_rule,
    evaluate_selection,
    mean_daily_auc,
    quarter_splits,
    select_candidates,
    walk_forward_predict,
)


def _synthetic(n_dates: int = 400, n_tickers: int = 30, seed: int = 1) -> pd.DataFrame:
    """Signal feature ``sig`` raises P(hit); ``noise`` is irrelevant."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_dates)
    rows = []
    for d in dates:
        vix = rng.uniform(12, 30)
        for t in range(n_tickers):
            sig = rng.normal()
            p = 1 / (1 + np.exp(-(0.4 + 1.2 * sig)))
            hit = rng.random() < p
            rows.append({
                "snapshot_date": d, "ticker": f"T{t:02d}", "sig": sig,
                "noise": rng.normal(), "macro_vix": vix,
                "atr_14_pct": rng.uniform(0.01, 0.04),
                "barrier_outcome": 1.0 if hit else -1.0,
                "barrier_ambiguous": bool(rng.random() < 0.05),
                "return_barrier_14d": 0.0095 if hit else -0.0205,
            })
    return pd.DataFrame(rows)


def test_break_even_hit_rate():
    assert BREAK_EVEN_HIT_RATE == pytest.approx(0.0205 / 0.03)
    assert PURGE_SESSIONS == 16


def test_mean_daily_auc_ignores_level_differences():
    # perfect ranking within each day, but day 2 has much higher P overall
    dates = np.array(["d1"] * 4 + ["d2"] * 4 + ["d3"] * 2)
    y = np.array([0, 0, 1, 1, 0, 0, 1, 1, 1, 1])
    p = np.array([0.1, 0.2, 0.3, 0.4, 0.8, 0.85, 0.9, 0.95, 0.5, 0.6])
    assert mean_daily_auc(dates, y, p) == pytest.approx(1.0)  # d3 (one class) skipped


class TestBuildDesign:
    def test_ranks_raw_and_labels(self):
        df = pd.DataFrame({
            "snapshot_date": ["2025-01-02"] * 3 + ["2025-01-03"] * 3,
            "ticker": list("ABC") * 2,
            "sig": [1.0, 2.0, 3.0, 30.0, 20.0, 10.0],
            "macro_vix": [15.0] * 3 + [25.0] * 3,
            "atr_14_pct": [0.01, 0.02, 0.03] * 2,
            "barrier_outcome": [1, -1, 0, 1, 1, np.nan],
            "barrier_ambiguous": [True, False, False, False, False, False],
            "return_barrier_14d": [0.0095, -0.0205, 0.001, 0.0095, 0.0095, np.nan],
        })
        x, meta = build_design(df, ["sig", "macro_vix", "atr_14_pct", "missing_col"])
        assert len(x) == len(meta) == 5  # unlabeled row dropped
        assert "macro_vix" not in x.columns  # market-wide only raw
        assert {"sig", "macro_vix__raw", "atr_14_pct", "atr_14_pct__raw"} <= set(x.columns)
        assert x["sig"].iloc[:3].tolist() == [0.0, 0.5, 1.0]
        assert meta["y"].tolist() == [1, 0, 0, 1, 1]
        # ambiguous take profit counts as stop in the conservative variant
        assert meta["net_cons"].iloc[0] == pytest.approx(STOP_NET)
        assert meta["net_cons"].iloc[3] == pytest.approx(0.0095)


class TestQuarterSplits:
    def test_purge_and_quarters(self):
        dates = pd.Series(np.repeat(pd.bdate_range("2023-01-02", "2024-12-31"), 2))
        splits = quarter_splits(dates, "2024-01-01", purge=16, min_train_dates=50)
        assert [s[0] for s in splits] == ["2024Q1", "2024Q2", "2024Q3", "2024Q4"]
        uniq = np.unique(dates.to_numpy())
        for q, tr, te in splits:
            test_dates = np.unique(dates.to_numpy()[te])
            train_dates = np.unique(dates.to_numpy()[tr])
            assert str(pd.Period(test_dates[0], "Q")) == q
            gap = np.searchsorted(uniq, test_dates[0]) - np.searchsorted(uniq, train_dates[-1])
            assert gap == 17  # 16 purged dates in between
            assert not set(tr) & set(te)

    def test_skips_folds_without_enough_history(self):
        dates = pd.Series(pd.bdate_range("2024-01-02", "2024-06-28"))
        # Q1: no history at all → skipped; Q2: 64 Q1 dates − 16 purged = 48 ≥ 40
        assert [s[0] for s in quarter_splits(dates, "2024-01-01", min_train_dates=40)] == ["2024Q2"]
        assert quarter_splits(dates, "2024-01-01", min_train_dates=50) == []


class TestSelection:
    def _preds(self):
        return pd.DataFrame({
            "snapshot_date": pd.to_datetime(["2025-01-02"] * 3 + ["2025-01-03"] * 3),
            "ticker": list("ABCABC"),
            "p_logit": [0.80, 0.70, 0.50, 0.60, 0.55, 0.40],
            "y": [1, 1, 0, 0, 1, 0],
            "net": [0.0095, 0.0095, -0.0205, -0.0205, 0.0095, -0.0205],
            "net_cons": [0.0095, 0.0095, -0.0205, -0.0205, 0.0095, -0.0205],
        })

    def test_top_k_and_threshold(self):
        p = self._preds()
        sel = select_candidates(p, Rule("logit", 2, 0.65))
        assert sel["ticker"].tolist() == ["A", "B"]  # day 2: nothing ≥ 0.65
        ev = evaluate_selection(sel, p, bootstrap=False)
        assert ev["days_with"] == 1 and ev["no_candidate_share"] == pytest.approx(0.5)
        assert ev["hit_rate"] == 1.0
        assert ev["uni_net"] == pytest.approx((0.0095 * 2 - 0.0205) / 3)

    def test_date_weighting(self):
        p = self._preds()
        ev = evaluate_selection(select_candidates(p, Rule("logit", 3, 0.0)), p, bootstrap=False)
        day1 = (0.0095 * 2 - 0.0205) / 3
        day2 = (-0.0205 + 0.0095 - 0.0205) / 3
        assert ev["net"] == pytest.approx((day1 + day2) / 2)

    def test_empty_selection(self):
        p = self._preds()
        ev = evaluate_selection(select_candidates(p, Rule("logit", 1, 0.99)), p)
        assert ev["n_trades"] == 0 and ev["no_candidate_share"] == 1.0


class TestWalkForward:
    def test_models_learn_signal_out_of_sample(self):
        df = _synthetic()
        x, meta = build_design(df, ["sig", "noise", "macro_vix", "atr_14_pct"])
        splits = quarter_splits(meta["snapshot_date"], "2023-10-01", min_train_dates=100)
        preds, folds = walk_forward_predict(x, meta, splits, ("logit", "gbm"))
        assert len(preds) == sum(te.size for _, _, te in splits)
        for f in folds:
            assert f["auc"] > 0.65, f
        rule, table = choose_rule(preds, ("logit", "gbm"), (1, 3), (0.0, 0.7), min_days=20)
        assert rule is not None
        best = table.loc[table["rule"] == rule].iloc[0]
        assert best["hit_rate"] > preds["y"].mean()

    def test_choose_rule_respects_min_days(self):
        df = _synthetic(n_dates=200, n_tickers=10)
        x, meta = build_design(df, ["sig", "noise"])
        splits = quarter_splits(meta["snapshot_date"], "2023-07-01", min_train_dates=60)
        preds, _ = walk_forward_predict(x, meta, splits, ("logit",))
        rule, _ = choose_rule(preds, ("logit",), (1,), (0.999,), min_days=10)
        assert rule is None

    def test_column_empty_in_training_window(self):
        """A source that starts late (all NaN in early folds) must not break the GBM."""
        df = _synthetic(n_dates=250, n_tickers=10)
        late = df["snapshot_date"] >= df["snapshot_date"].unique()[200]
        df["options_iv_late"] = np.where(late, np.random.default_rng(0).normal(size=len(df)), np.nan)
        x, meta = build_design(df, ["sig", "options_iv_late"])
        splits = quarter_splits(meta["snapshot_date"], "2023-07-01", min_train_dates=60)
        preds, folds = walk_forward_predict(x, meta, splits, ("gbm", "logit"))
        assert len(folds) == 2 * len(splits)
        assert preds["p_gbm"].notna().all()
