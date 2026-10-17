"""Unit tests for trading_signals.analysis.stats (pure helpers, no DB)."""

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from trading_signals.analysis.stats import (
    available_features,
    batch_jackknife,
    batch_jackknife_diff,
    batch_jackknife_mean,
    block_bootstrap_diff,
    block_bootstrap_indices,
    bootstrap_ci,
    ci_supports_verdict,
    cross_sectional_transform,
    daily_group_difference,
    daily_rank_ic,
    date_block_bootstrap,
    excess_returns,
    has_batch_ci,
    has_block_ci,
    horizon_from_target,
    ic_difference,
    ic_summary,
    jackknife_batches,
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


def test_block_bootstrap_diff_detects_shift():
    # moved from analysis/barrier.py (plan 2026-10-17, step 2)
    idx = pd.date_range("2026-01-01", periods=200)
    rng = np.random.default_rng(1)
    a = pd.Series(rng.normal(0.01, 0.001, 200), index=idx)
    b = pd.Series(rng.normal(0.0, 0.001, 200), index=idx)
    diff, lo, hi = block_bootstrap_diff(a, b, block=10, n_boot=300)
    assert lo > 0 and diff == pytest.approx(0.01, abs=0.001)
    assert lo < diff < hi


def test_block_bootstrap_diff_edge_cases():
    idx = pd.date_range("2026-01-01", periods=30)
    a = pd.Series(np.linspace(0, 1, 30), index=idx)
    # identical series -> zero difference, degenerate CI
    diff, lo, hi = block_bootstrap_diff(a, a, block=5, n_boot=50)
    assert diff == 0 and lo == 0 and hi == 0
    # only common dates count, a block longer than the sample is clipped
    diff, lo, hi = block_bootstrap_diff(a, a.iloc[:3] * 0, block=100, n_boot=20)
    assert np.isfinite(diff)
    # no overlap -> NaN triple
    other = pd.Series([1.0], index=pd.to_datetime(["2030-01-01"]))
    assert all(np.isnan(v) for v in block_bootstrap_diff(a, other, n_boot=5))
    empty = block_bootstrap_diff(a.iloc[:0], a.iloc[:0], n_boot=5)
    assert all(np.isnan(v) for v in empty)
    # deterministic with the default seed
    x = block_bootstrap_diff(a, a * 0.5, block=5, n_boot=100)
    assert x == block_bootstrap_diff(a, a * 0.5, block=5, n_boot=100)


def test_has_block_ci_needs_two_blocks():
    assert has_block_ci(40, 20) and has_block_ci(120, 60)
    assert not has_block_ci(39, 20) and not has_block_ci(80, 60)
    assert has_block_ci(2, 0) and not has_block_ci(1, 1)  # block floor of 1


# ── batch jackknife (review M1) ──────────────────────────────────────


def test_batch_counts_and_thresholds():
    # batches of 2 x horizon sessions; an interval needs two of them, a verdict
    # marker ten horizons of data
    assert jackknife_batches(200, 20) == 5 and jackknife_batches(79, 20) == 1
    assert jackknife_batches(80, 20) == 2 and jackknife_batches(5, 0) == 2
    assert has_batch_ci(80, 20) and not has_batch_ci(79, 20)
    assert not has_batch_ci(1, 1) and has_batch_ci(4, 1)
    assert ci_supports_verdict(200, 20) and not ci_supports_verdict(199, 20)
    assert ci_supports_verdict(600, 60) and not ci_supports_verdict(599, 60)
    assert jackknife_batches(200, 20, factor=1) == 10  # simulation comparison only


def test_batch_jackknife_of_a_mean_is_the_batch_means_t_interval():
    from scipy import stats as sps

    x = np.random.default_rng(3).normal(size=80)
    est, lo, hi = batch_jackknife(x[:, None], {"m": lambda a: float(a[:, 0].mean())},
                                  horizon=10)["m"]  # 4 batches of 20
    means = x.reshape(4, 20).mean(axis=1)
    se = means.std(ddof=1) / 2.0
    crit = sps.t.ppf(0.975, 3)
    assert est == pytest.approx(x.mean())
    assert (lo, hi) == pytest.approx((x.mean() - crit * se, x.mean() + crit * se))
    # deterministic: no seed involved
    assert batch_jackknife_mean(x, 10) == batch_jackknife_mean(x, 10)
    assert batch_jackknife_mean(x, 10) == pytest.approx((est, lo, hi))


def test_batch_jackknife_ragged_batches_and_several_statistics():
    x = np.random.default_rng(4).normal(size=90)  # 4 batches of 23 / 22 rows
    y = x ** 2
    arr = np.column_stack([x, y])
    res = batch_jackknife(arr, {"a": lambda m: float(m[:, 0].mean()),
                                "d": lambda m: float(m[:, 1].mean() - m[:, 0].mean())},
                          horizon=10)
    assert set(res) == {"a", "d"}
    for est, lo, hi in res.values():
        assert lo < est < hi
    # a statistic that is NaN when a batch is left out has no interval
    est, lo, hi = batch_jackknife(arr, {"n": lambda m: float(m[0, 0])}, 10)["n"]
    assert est == pytest.approx(x[0]) and np.isfinite(est)
    assert np.isnan(batch_jackknife(
        arr, {"n": lambda m: float("nan") if len(m) < 80 else 1.0}, 10)["n"][1])


def test_batch_jackknife_needs_two_batches():
    x = np.arange(39, dtype=float)
    est, lo, hi = batch_jackknife_mean(x, 10)  # 39 // 20 = 1 batch
    assert est == pytest.approx(19.0) and np.isnan(lo) and np.isnan(hi)
    assert all(np.isnan(v) for v in batch_jackknife_mean([], 10))
    assert all(np.isnan(v) for v in batch_jackknife_mean([np.nan, np.nan], 10))
    # NaNs inside a series are dropped
    y = np.concatenate([np.random.default_rng(0).normal(size=60), [np.nan] * 5])
    assert np.isfinite(batch_jackknife_mean(y, 10)[1])


def test_batch_jackknife_diff_uses_the_common_dates_only():
    idx = pd.date_range("2024-01-01", periods=100, freq="B")
    rng = np.random.default_rng(5)
    a = pd.Series(0.02 + rng.normal(scale=0.01, size=100), index=idx)
    b = pd.Series(rng.normal(scale=0.01, size=100), index=idx)
    est, lo, hi = batch_jackknife_diff(a, b, 10)
    assert est == pytest.approx((a - b).mean()) and lo > 0 > -lo and lo < est < hi
    # dates present in one series only drop out
    est2, _, _ = batch_jackknife_diff(a.iloc[:60], b.iloc[20:], 10)
    assert est2 == pytest.approx((a.iloc[20:60] - b.iloc[20:60]).mean())
    # a difference is order-sensitive but symmetric in sign
    e3, lo3, hi3 = batch_jackknife_diff(b, a, 10)
    assert (e3, lo3, hi3) == pytest.approx((-est, -hi, -lo))
    assert all(np.isnan(v) for v in batch_jackknife_diff(a.iloc[:0], b.iloc[:0], 10))


def test_batch_jackknife_is_wider_than_the_naive_iid_interval_on_overlapping_sums():
    # 20-day sums of iid daily shocks: the mean of the overlapping series has
    # about 20 times the variance of an iid sample of that size
    rng = np.random.default_rng(6)
    daily = rng.normal(size=400 + 20)
    sums = np.convolve(daily, np.ones(20), mode="valid")[:400]
    est, lo, hi = batch_jackknife_mean(sums, 20)  # 10 batches of 40
    naive = 1.96 * sums.std(ddof=1) / np.sqrt(len(sums))
    assert (hi - lo) / 2 > 2.5 * naive
