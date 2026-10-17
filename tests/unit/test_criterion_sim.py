"""Size / power simulation of the decisive criterion (review M1), reduced for CI.

The full study is ``scripts/analysis/simulate_criterion_size.py`` (thousands of
worlds); here a few dozen small worlds check that the simulation measures the
real criterion code and that the production interval keeps its size.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_signals.analysis import criterion_sim as cs
from trading_signals.analysis import forecast_eval as fe

#: A small world (80 names, 5 calls per side) keeps each run in milliseconds.
SMALL = cs.Scenario(n_tickers=80, calls_per_side=5)


def test_simulated_world_shape_and_determinism():
    sc = cs.Scenario(n_sessions=30, n_tickers=40, horizon=5, calls_per_side=3)
    fc, uni = cs.simulate_world(sc, np.random.default_rng(1))
    assert len(uni) == 30 * 40 and "return_5d" in uni.columns
    assert len(fc) == 30 * 2 * 3
    per_day = fc.groupby(["session_date", "direction"]).size()
    assert (per_day == 3).all()
    # one call per ticker and session
    assert not fc.duplicated(["session_date", "ticker"]).any()
    fc2, uni2 = cs.simulate_world(sc, np.random.default_rng(1))
    pd.testing.assert_frame_equal(fc, fc2)
    pd.testing.assert_frame_equal(uni, uni2)
    other, _ = cs.simulate_world(sc, np.random.default_rng(2))
    assert not other["ticker"].equals(fc["ticker"])


def test_persistent_calls_repeat_the_same_names_on_consecutive_days():
    sc = cs.Scenario(n_sessions=60, n_tickers=100, calls_per_side=8,
                     persistence=0.95)
    fc, _ = cs.simulate_world(sc, np.random.default_rng(2))
    up = fc[fc["direction"] == "up"].groupby("session_date")["ticker"].apply(set)
    overlaps = [len(a & b) / 8 for a, b in zip(up.iloc[:-1], up.iloc[1:], strict=True)]
    assert np.mean(overlaps) > 0.5
    fresh = cs.Scenario(n_sessions=60, n_tickers=100, calls_per_side=8, persistence=0.0)
    fc0, _ = cs.simulate_world(fresh, np.random.default_rng(2))
    up0 = fc0[fc0["direction"] == "up"].groupby("session_date")["ticker"].apply(set)
    pairs0 = zip(up0.iloc[:-1], up0.iloc[1:], strict=True)
    overlap0 = [len(a & b) / 8 for a, b in pairs0]
    assert np.mean(overlap0) < 0.2


def test_sector_scenario_clusters_the_calls_in_few_sectors():
    plain = cs.Scenario(n_sessions=60, n_tickers=100, calls_per_side=8)
    clustered = cs.Scenario(n_sessions=60, n_tickers=100, calls_per_side=8,
                            n_sectors=10, sector_sd=0.01, sector_weight=0.8)

    def mean_sectors(sc: cs.Scenario) -> float:
        fc, _ = cs.simulate_world(sc, np.random.default_rng(6))
        up = fc[fc["direction"] == "up"].copy()
        up["sector"] = up["ticker"].str[1:].astype(int) % 10
        return float(up.groupby("session_date")["sector"].nunique().mean())

    assert mean_sectors(clustered) < mean_sectors(plain) - 1.5
    assert "calls clustered in sectors" in cs.SCENARIOS


def test_returns_overlap_so_neighbouring_sessions_are_correlated():
    sc = cs.Scenario(n_sessions=120, n_tickers=60, horizon=20, calls_per_side=5,
                     persistence=0.5)
    fc, uni = cs.simulate_world(sc, np.random.default_rng(3))
    wide = uni.pivot(index="snapshot_date", columns="ticker", values="return_20d")
    # the 20-day return of a name on consecutive sessions shares 19 of 20 daily
    # shocks: correlation ~ 0.95; 40 sessions apart there is no overlap
    near = np.mean([wide[c].autocorr(1) for c in wide.columns])
    far = np.mean([wide[c].autocorr(40) for c in wide.columns])
    assert near > 0.8 and abs(far) < 0.3


def test_the_production_method_is_the_code_of_the_real_criterion():
    rng = np.random.default_rng(4)
    for skill in (0.0, 0.0005):
        sc = cs.with_skill(SMALL, skill)
        fc, uni = cs.simulate_world(sc, rng)
        table = cs.session_table(fc, uni, sc.horizon)
        lo = cs.lower_bounds(cs.PRODUCTION_METHOD, table, sc.horizon, rng)
        real = fe.primary_criterion(fc, uni, min_dates=sc.n_sessions,
                                    min_per_side=10)
        assert real["status"] in ("passed", "failed")
        checks = real["modes"]["system"]["checks"]
        assert lo["relative"] == pytest.approx(
            checks["relative_up_minus_down"]["ci_low"])
        assert lo["selection"] == pytest.approx(checks["selection_hit_edge"]["ci_low"])
        both_pass = lo["relative"] > 0 and lo["selection"] > 0
        assert (real["status"] == "passed") == both_pass


@pytest.mark.parametrize("method", cs.METHODS)
def test_every_method_returns_finite_lower_bounds(method):
    rng = np.random.default_rng(5)
    fc, uni = cs.simulate_world(SMALL, rng)
    table = cs.session_table(fc, uni, SMALL.horizon)
    lo = cs.lower_bounds(method, table, SMALL.horizon, rng, n_boot=200)
    assert set(lo) == set(cs.STATS) and all(np.isfinite(v) for v in lo.values())
    with pytest.raises(ValueError, match="unknown method"):
        cs.lower_bounds("bagging", table, SMALL.horizon, rng)


@pytest.mark.parametrize("persistence", [0.0, 0.95, 0.99])
def test_the_production_interval_keeps_its_size_without_skill(persistence):
    sc = cs.Scenario(n_tickers=80, calls_per_side=5, persistence=persistence)
    res = cs.pass_rates(sc, n_runs=60, seed=100 + int(persistence * 100),
                        methods=(cs.PRODUCTION_METHOD,)).iloc[0]
    # nominal 2.5 % per statistic, far less jointly; 60 runs give a loose bound
    assert res["joint"] <= 0.08 and res["relative"] <= 0.12 and res["selection"] <= 0.12
    assert abs(res["mean_relative_estimate"]) < 0.01  # no skill, no edge


def test_a_planted_edge_is_found_most_of_the_time():
    sc = cs.with_skill(SMALL, 0.001)
    res = cs.pass_rates(sc, n_runs=30, seed=7, methods=(cs.PRODUCTION_METHOD,)).iloc[0]
    assert res["mean_relative_estimate"] > 0.02
    assert res["joint"] >= 0.8
    # a weaker edge is found less often (power rises with the edge)
    weak = cs.pass_rates(cs.with_skill(SMALL, 0.0002), n_runs=30, seed=7,
                         methods=(cs.PRODUCTION_METHOD,)).iloc[0]
    assert weak["joint"] < res["joint"]


def test_pass_rates_compares_methods_on_the_same_worlds():
    res = cs.pass_rates(cs.Scenario(n_sessions=100, n_tickers=40, calls_per_side=4),
                        n_runs=5, seed=1, methods=("jackknife_2h", "jackknife_h"),
                        n_boot=100)
    assert list(res["method"]) == ["jackknife_2h", "jackknife_h"]
    assert (res["n_runs"] == 5).all()
    assert res["mean_relative_estimate"].nunique() == 1  # one set of worlds
    assert ((res[["relative", "selection", "joint"]] >= 0) & (
        res[["relative", "selection", "joint"]] <= 1)).all().all()
    assert (res["joint"] <= res[["relative", "selection"]].min(axis=1)).all()


def test_scenario_table_and_methods_are_consistent():
    assert cs.PRODUCTION_METHOD in cs.METHODS
    assert len(set(cs.METHODS)) == len(cs.METHODS)
    assert all(sc.skill == 0.0 for sc in cs.SCENARIOS.values())
    assert cs.SCENARIOS["base (rho 0.95)"] == cs.Scenario()
