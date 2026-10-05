"""Unit tests for trading_signals.analysis.stats (pure helpers, no DB)."""

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from trading_signals.analysis.stats import (
    available_features,
    block_bootstrap_indices,
    bootstrap_ci,
    cross_sectional_transform,
    daily_group_difference,
    daily_rank_ic,
    date_block_bootstrap,
    excess_returns,
    horizon_from_target,
    ic_difference,
    ic_summary,
    mean_test,
    newey_west_se,
    newey_west_tstat,
    nw_lag_for_horizon,
    purged_walk_forward_splits,
    quintile_returns,
    to_float,
)


def _panel(n_dates=30, n_tickers=25, seed=0, beta=1.0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_dates)
    rows = []
    for d in dates:
        x = rng.normal(size=n_tickers)
        y = beta * x + rng.normal(size=n_tickers) + rng.normal() * 5  # date shock
        for i in range(n_tickers):
            rows.append({"snapshot_date": d, "ticker": f"T{i}", "x": x[i], "y": y[i]})
    return pd.DataFrame(rows)


# ── type helpers ─────────────────────────────────────────────────────


def test_to_float_handles_bool_object_and_none():
    s = pd.Series([True, None, False, "abc"], dtype=object)
    out = to_float(s)
    assert out.iloc[0] == 1.0 and out.iloc[2] == 0.0
    assert np.isnan(out.iloc[1]) and np.isnan(out.iloc[3])
    assert to_float(pd.Series([True, False])).tolist() == [1.0, 0.0]


def test_available_features_filters_missing_and_all_nan():
    df = pd.DataFrame({"rsi_14": [1.0, 2.0], "pe_ratio": [np.nan, np.nan],
                       "ark_multi_etf_signal": [True, False], "foo": [1, 2]})
    feats = available_features(df)
    assert "rsi_14" in feats and "ark_multi_etf_signal" in feats
    assert "pe_ratio" not in feats and "foo" not in feats


def test_horizon_and_lag():
    assert horizon_from_target("return_20d") == 20
    assert horizon_from_target("return_1d") == 1
    assert nw_lag_for_horizon(1) == 1
    assert nw_lag_for_horizon(20) == 19
    with pytest.raises(ValueError):
        horizon_from_target("foo")


# ── Newey-West ───────────────────────────────────────────────────────


def test_newey_west_lag0_equals_iid_se():
    x = np.random.default_rng(1).normal(0.1, 1.0, 500)
    se = newey_west_se(x, 0)
    assert se == pytest.approx(x.std(ddof=0) / np.sqrt(len(x)))
    assert newey_west_tstat(x, 0) == pytest.approx(x.mean() / se)


def test_newey_west_penalises_positive_autocorrelation():
    rng = np.random.default_rng(2)
    e = rng.normal(size=2000)
    # MA(19) -> strong positive autocorrelation like overlapping 20d targets
    x = 0.05 + np.convolve(e, np.ones(20) / 20, mode="valid")
    t_iid = newey_west_tstat(x, 0)
    t_nw = newey_west_tstat(x, 19)
    assert abs(t_nw) < abs(t_iid) / 2


def test_mean_test_and_ic_summary_keys():
    ic = pd.Series(np.random.default_rng(3).normal(0.05, 0.1, 250))
    s = ic_summary(ic, horizon=5)
    assert set(s) == {"mean_ic", "ic_std", "icir", "t_nw", "pvalue", "n_dates"}
    assert s["n_dates"] == 250
    assert s["icir"] == pytest.approx(s["mean_ic"] / s["ic_std"])
    assert 0 <= s["pvalue"] <= 1
    assert mean_test([], 1)["n"] == 0


# ── daily rank IC ────────────────────────────────────────────────────


def test_daily_rank_ic_matches_scipy_per_date():
    df = _panel(n_dates=5)
    df.loc[3, "x"] = np.nan  # pairwise-complete handling
    ic = daily_rank_ic(df, "x", "y", min_obs=5)
    assert len(ic) == 5
    for d, val in ic.items():
        sub = df[df.snapshot_date == d].dropna()
        assert val == pytest.approx(spearmanr(sub.x, sub.y)[0], abs=1e-10)


def test_daily_rank_ic_skips_small_and_constant_dates():
    df = _panel(n_dates=4, n_tickers=12)
    d0, d1 = df.snapshot_date.unique()[:2]
    df.loc[df.snapshot_date == d0, "x"] = 1.0  # constant feature
    df = df[~((df.snapshot_date == d1) & (df.ticker.isin([f"T{i}" for i in range(5)])))]
    ic = daily_rank_ic(df, "x", "y", min_obs=10)
    assert d0 not in ic.index and d1 not in ic.index
    assert len(ic) == 2


def test_daily_rank_ic_invariant_to_date_shock():
    """Daily IC ignores the per-date market shock that dilutes pooled corr."""
    df = _panel(n_dates=40, beta=0.5)
    ic = daily_rank_ic(df, "x", "y").mean()
    pooled = spearmanr(df.x, df.y)[0]
    assert ic > 0.3
    assert pooled < ic  # pooled diluted by the date shocks


def test_ic_difference_aligned():
    df = _panel(n_dates=15)
    df["z"] = np.random.default_rng(5).normal(size=len(df))
    a, b = ic_difference(df, "x", "z", "y")
    assert a.index.equals(b.index) and len(a) == 15
    assert a.mean() > b.mean()


# ── transforms ───────────────────────────────────────────────────────


def test_cross_sectional_rank_and_zscore():
    df = pd.DataFrame({
        "snapshot_date": ["d1"] * 4 + ["d2"] * 3,
        "a": [10.0, 20.0, np.nan, 30.0, 5.0, 5.0, 5.0],
    })
    r = cross_sectional_transform(df, ["a"], "rank")
    assert r["a"].iloc[:4].tolist()[:2] == [0.0, 0.5]
    assert np.isnan(r["a"].iloc[2]) and r["a"].iloc[3] == 1.0
    assert r["a"].iloc[4:].tolist() == [0.5, 0.5, 0.5]  # constant -> 0.5
    rc = cross_sectional_transform(df, ["a"], "rank", centered=True)
    assert rc["a"].iloc[0] == -0.5
    z = cross_sectional_transform(df, ["a"], "zscore")
    assert z["a"].iloc[[0, 1, 3]].mean() == pytest.approx(0.0)
    assert z["a"].iloc[4:].tolist() == [0.0, 0.0, 0.0]
    assert df["a"].iloc[0] == 10.0  # input unchanged
    with pytest.raises(ValueError):
        cross_sectional_transform(df, ["a"], "bogus")


def test_rank_transform_scale_invariant():
    df = _panel(n_dates=3)
    a = cross_sectional_transform(df, ["x"], "rank")["x"]
    df2 = df.assign(x=df.x * 1e6 + 7)
    b = cross_sectional_transform(df2, ["x"], "rank")["x"]
    pd.testing.assert_series_equal(a, b)


def test_excess_returns_cross_sectional_and_benchmark():
    df = pd.DataFrame({
        "snapshot_date": ["d1", "d1", "d2", "d2"],
        "return_5d": [0.1, 0.3, -0.2, np.nan],
        "spy": [0.05, 0.05, 0.0, 0.0],
    })
    out = excess_returns(df, ["return_5d"])
    assert out["return_5d"].tolist()[:3] == pytest.approx([-0.1, 0.1, 0.0])
    assert np.isnan(out["return_5d"].iloc[3])
    out2 = excess_returns(df, ["return_5d"], benchmark={"return_5d": "spy"})
    assert out2["return_5d"].iloc[0] == pytest.approx(0.05)


def test_daily_group_difference_requires_min_group():
    df = pd.DataFrame({
        "snapshot_date": ["d1"] * 6 + ["d2"] * 4,
        "y": [1, 1, 1, 0, 0, 0, 5, 5, 0, 0],
    })
    grp = pd.Series([1, 1, 1, 0, 0, 0, 1, 1, 0, np.nan])
    diff = daily_group_difference(df, grp, "y", min_group=3)
    assert list(diff.index) == ["d1"]
    assert diff.iloc[0] == pytest.approx(1.0)


def test_quintile_returns_per_date_and_long_short():
    df = _panel(n_dates=30, n_tickers=50, beta=1.0)
    res = quintile_returns(df, "x", "y", horizon=1)
    qm = res["quantile_means"]
    assert list(qm.index) == [1, 2, 3, 4, 5]
    assert qm.is_monotonic_increasing
    assert res["long_short_mean"] > 0 and res["long_short_t_nw"] > 3
    assert res["n_dates"] == 30
    # per-date assignment: every date has all five buckets
    assert res["daily"].notna().all().all()


def test_quintile_returns_binary_feature_uses_nonempty_extremes():
    df = _panel(n_dates=10, n_tickers=30)
    df["b"] = (df.x > 0.5).astype(float)
    res = quintile_returns(df, "b", "y", horizon=1)
    assert res["n_dates"] == 10
    assert len(res["long_short"]) > 0


# ── purged walk-forward ──────────────────────────────────────────────


def test_purged_walk_forward_splits_purge_and_order():
    dates = np.repeat(pd.bdate_range("2024-01-01", periods=120).to_numpy(), 3)
    splits = purged_walk_forward_splits(dates, n_splits=4, horizon=10)
    assert len(splits) == 4
    uniq = np.unique(dates)
    prev_test_end = None
    for tr, te in splits:
        tr_d, te_d = np.unique(dates[tr]), np.unique(dates[te])
        start = np.searchsorted(uniq, te_d.min())
        assert tr_d.max() < te_d.min()
        # last training date at least `horizon` dates before test start
        assert np.searchsorted(uniq, tr_d.max()) <= start - 10 - 1
        # all rows of a date in one fold
        assert len(np.intersect1d(dates[tr], dates[te])) == 0
        if prev_test_end is not None:
            assert te_d.min() > prev_test_end
        prev_test_end = te_d.max()


def test_purged_walk_forward_min_train_and_kfold_embargo():
    dates = pd.bdate_range("2024-01-01", periods=60).to_numpy()
    assert purged_walk_forward_splits(dates, 5, 20, min_train_dates=1000) == []
    kf = purged_walk_forward_splits(dates, 3, horizon=5, embargo=7, expanding=False)
    assert len(kf) == 3
    tr, te = kf[0]  # first block: train only after test + embargo
    assert tr.min() >= te.max() + 1 + 7
    tr, te = kf[1]
    before = tr[tr < te.min()]
    after = tr[tr > te.max()]
    assert before.max() <= te.min() - 5 - 1
    assert after.min() >= te.max() + 1 + 7


# ── bootstrap ────────────────────────────────────────────────────────


def test_block_bootstrap_indices_shape_and_contiguity():
    idx = list(block_bootstrap_indices(23, 5, 3, seed=1))
    assert len(idx) == 3 and all(len(i) == 23 for i in idx)
    first = idx[0]
    assert np.all(np.diff(first[:5]) == 1)


def test_date_block_bootstrap_ci_and_autocorrelation():
    rng = np.random.default_rng(7)
    e = rng.normal(size=600)
    x = 0.02 + np.convolve(e, np.ones(20) / 20, mode="valid")
    iid = date_block_bootstrap(x, 1, n_boot=400, seed=0)
    blk = date_block_bootstrap(x, 20, n_boot=400, seed=0)
    assert blk.std() > 1.5 * iid.std()
    lo, hi = bootstrap_ci(blk)
    assert lo < x.mean() < hi
    # deterministic with seed
    assert np.array_equal(blk, date_block_bootstrap(x, 20, n_boot=400, seed=0))


def test_date_block_bootstrap_dataframe_mode():
    df = _panel(n_dates=20, n_tickers=12)
    samples = date_block_bootstrap(
        df, 5, n_boot=20, seed=1,
        statistic=lambda d: daily_rank_ic(d, "x", "y").mean(),
    )
    assert samples.shape == (20,) and np.all(samples > 0)
    with pytest.raises(ValueError):
        date_block_bootstrap(df, 5, n_boot=2)
