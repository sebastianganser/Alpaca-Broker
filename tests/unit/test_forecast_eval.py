"""Unit tests for trading_signals.analysis.forecast_eval (synthetic, no DB)."""

from __future__ import annotations

import inspect
import logging
from datetime import date

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from tests.unit.forecast_data import D1, D2, D3, SPY, forecasts, universe
from trading_signals.analysis import forecast_eval as fe

# ── hand-checkable case: 3 sessions, 4 tickers (tests/unit/forecast_data.py) ──


@pytest.fixture(scope="module")
def hand():
    joined = fe.attach_outcomes(forecasts(), universe(), SPY)
    return joined, fe.evaluate_direction(joined, 20)


def test_attach_outcomes_values(hand):
    joined, _ = hand
    a1 = joined[(joined["session_date"] == D1) & (joined["ticker"] == "A")].iloc[0]
    assert a1["ret"] == pytest.approx(0.10) and a1["uni_mean"] == pytest.approx(0.0)
    assert a1["ex_uni"] == pytest.approx(0.10) and a1["ex_spy"] == pytest.approx(0.09)
    assert a1["hit"] == 1 and a1["base_hit"] == pytest.approx(0.5)
    b1 = joined[(joined["session_date"] == D1) & (joined["ticker"] == "B")].iloc[0]
    assert b1["hit"] == 0  # called down, went up
    c1 = joined[(joined["session_date"] == D1) & (joined["ticker"] == "C")].iloc[0]
    assert np.isnan(c1["hit"]) and np.isnan(c1["base_hit"])  # neutral: no hit
    # base rate of an up call = share of the universe that rose that session
    a3 = joined[(joined["session_date"] == D3)].iloc[0]
    assert a3["base_hit"] == pytest.approx(0.25)
    c2 = joined[(joined["session_date"] == D2) & (joined["ticker"] == "C")].iloc[0]
    assert c2["base_hit"] == pytest.approx(0.0)  # nobody fell on D2
    assert (joined["horizon_days"] == 20).all()
    # natural key and the default version are attached
    assert a1["forecast_id"] == "2024-03-01|A|20"
    assert (joined["forecaster_version"] == fe.UNVERSIONED).all()


def test_counts_and_reliability(hand):
    _, r = hand
    assert (r["n_forecasts"], r["n_up"], r["n_down"], r["n_neutral"]) == (8, 4, 3, 1)
    assert r["n_dates"] == 3
    assert r["min_dates"] == 200 and r["reliable"] is False
    assert fe.reliable_dates(20) == 200 and fe.reliable_dates(60) == 600
    assert fe.reliable_dates(5) == 50


def test_selection_hit_edge_is_a_cross_sectional_measure(hand):
    _, r = hand
    # D1: 2 of 3 hit vs base .5; D2: 2 of 3 vs (1 + 1 + 0) / 3; D3: 1 of 1 vs .25
    assert r["hit_rate"] == pytest.approx(np.mean([2 / 3, 2 / 3, 1.0]))
    assert r["selection_base_rate"] == pytest.approx(np.mean([0.5, 2 / 3, 0.25]))
    est, lo, hi = r["selection_hit_edge"]
    assert est == pytest.approx(np.mean([2 / 3 - 0.5, 0.0, 0.75]))
    # three sessions hold no batch of 2 x 20 sessions: an estimate, no interval
    assert np.isnan(lo) and np.isnan(hi)
    assert "edge" not in r and "base_rate" not in r  # the old names are gone
    assert r["n_obs"]["selection_hit_edge"] == 3


def test_up_minus_down_absolute_and_relative(hand):
    _, r = hand
    # up: D1 .10, D2 mean(.08, .02) = .05, D3 .02 ; down: D1 mean(-.10, .02), D2 .04
    assert r["up_raw"][0] == pytest.approx(np.mean([0.10, 0.05, 0.02]))
    assert r["down_raw"][0] == pytest.approx(np.mean([-0.04, 0.04]))
    assert r["up_minus_down_raw"][0] == pytest.approx(0.17 / 3 - 0.0)
    # relative to the universe mean (0, .05, 0): up excess .10, 0, .02 ; down -.04, -.01
    assert r["up_ex_uni"][0] == pytest.approx(0.04)
    assert r["down_ex_uni"][0] == pytest.approx(-0.025)
    assert r["up_minus_down_ex_uni"][0] == pytest.approx(0.065)
    # relative to SPY (.01, .03, 0)
    assert r["up_ex_spy"][0] == pytest.approx(np.mean([0.09, 0.02, 0.02]))
    assert r["down_ex_spy"][0] == pytest.approx(np.mean([-0.05, 0.01]))
    assert r["up_minus_down_ex_spy"][0] == pytest.approx(0.13 / 3 + 0.02)
    # market-neutral on the two sessions that have both directions
    assert r["n_paired_dates"] == 2
    assert r["paired_up_minus_down"][0] == pytest.approx(
        np.mean([0.10 - (-0.04), 0.05 - 0.04]))
    # a difference is only as well-supported as its thinner side
    assert r["n_obs"]["up_minus_down_ex_uni"] == 2  # down calls on two sessions


def test_signed_returns_follow_the_calls(hand):
    _, r = hand
    # sigma * excess: D1 mean(.10, +.10, -.02), D2 mean(.03, -.03, +.01), D3 .02
    d1 = np.mean([0.10, 0.10, -0.02])
    d2 = np.mean([0.03, -0.03, 0.01])
    assert r["signed_ex_uni"][0] == pytest.approx(np.mean([d1, d2, 0.02]))


def test_true_absolute_block_is_raw_and_independent_of_the_universe(hand):
    _, r = hand
    # up calls all rose (raw hit 1.0); down calls: D1 1 of 2, D2 0 of 1 -> .25
    assert r["up_hit"][0] == pytest.approx(1.0)
    assert r["down_hit"][0] == pytest.approx(np.mean([0.5, 0.0]))
    # the references are passed in (the long-run shares of the universe frame)
    assert fe.long_run_shares(universe(), 20) == pytest.approx((7 / 12, 3 / 12))
    res = fe.evaluate_forecasts(forecasts(), universe(), SPY, horizons=(20,))
    h20 = res["horizons"][20]
    assert h20["long_run_pos_share"] == pytest.approx(7 / 12)
    assert h20["long_run_neg_share"] == pytest.approx(3 / 12)
    text = fe.render_report(res, date(2026, 10, 17))
    assert "Absolut (nur beschreibend, nicht entscheidend)" in text
    assert "Rohe Trefferquote" in text and "Langfrist" in text


def test_calibration_brier_reference_is_a_constant_known_at_time_d(hand):
    _, r = hand
    conf = np.array([0.60, 0.70, 0.55, 0.80, 0.65, 0.60, 0.90])
    hit = np.array([1, 1, 0, 1, 1, 0, 1], dtype=float)
    assert r["brier"] == pytest.approx(np.mean((conf - hit) ** 2))
    # primary reference: always saying 0.5 (= 0.25 whatever happens), never the
    # realised same-day base rate
    assert r["brier_ref"] == pytest.approx(0.25)
    assert r["brier_skill"] == pytest.approx(1 - r["brier"] / 0.25)
    # information only: the pooled whole-sample hit rate
    assert r["pooled_hit_rate"] == pytest.approx(5 / 7)
    assert r["brier_ref_pooled"] == pytest.approx(5 / 7 * 2 / 7)
    assert r["brier_skill_pooled"] == pytest.approx(1 - r["brier"] / (10 / 49))
    cal = r["calibration"].set_index("bin")
    assert cal["n"].sum() == 7  # the neutral forecast is not calibrated
    low = cal.iloc[0]  # [0.5, 0.6): .55 only
    assert low["n"] == 1 and low["hit_rate"] == 0.0
    assert r["mean_confidence"] == pytest.approx(conf.mean())


# ── preparation and validation ────────────────────────────────────────


def test_prepare_forecasts_normalises_and_validates():
    f = pd.DataFrame({"session_date": ["2024-03-01"], "ticker": ["nvda"],
                      "horizon_days": [60], "direction": ["UP"],
                      "confidence": ["0.6"], "conviction": [4]})
    p = fe.prepare_forecasts(f)
    assert p["ticker"].iloc[0] == "NVDA" and p["direction"].iloc[0] == "up"
    assert p["sigma"].iloc[0] == 1 and p["signed_conviction"].iloc[0] == 4
    assert p["confidence"].iloc[0] == pytest.approx(0.6)
    assert bool(p["on_time"].iloc[0]) is True
    assert p["forecaster_version"].iloc[0] == fe.UNVERSIONED
    assert p["forecast_id"].iloc[0] == "2024-03-01|NVDA|60"
    # idempotent, and an explicit version / id survive
    again = fe.prepare_forecasts(p.assign(forecaster_version=" skill-v2 "))
    assert again["forecaster_version"].iloc[0] == "skill-v2"
    assert again["forecast_id"].iloc[0] == "2024-03-01|NVDA|60"
    with pytest.raises(ValueError, match="unknown directions"):
        fe.prepare_forecasts(f.assign(direction="sideways"))
    with pytest.raises(ValueError, match="lack columns"):
        fe.prepare_forecasts(f.drop(columns="ticker"))
    neutral = fe.prepare_forecasts(f.assign(direction="neutral"))
    assert neutral["signed_conviction"].iloc[0] == 0


def test_pending_forecasts_are_not_scored():
    f = forecasts()
    uni = universe()
    uni.loc[uni["snapshot_date"] == D3, "return_20d"] = np.nan  # window still open
    res = fe.evaluate_forecasts(f, uni, SPY)
    r = res["horizons"][20]
    assert r["pending"] == 1 and r["n_dates"] == 2
    assert r["n_up"] == 3  # the D3 call is not scored yet
    assert res["n_forecasts"] == 8


def test_missing_return_column_is_skipped_with_a_log_line(caplog):
    f = forecasts().assign(horizon_days=60)
    with caplog.at_level(logging.WARNING, logger="analysis.forecast_eval"):
        joined = fe.attach_outcomes(f, universe().drop(columns="return_60d"))
    assert len(joined) == 8 and joined["ret"].isna().all()  # kept, not scored
    assert "return_60d" in caplog.text and "not scored" in caplog.text
    res = fe.evaluate_forecasts(f, universe().drop(columns="return_60d"))
    assert res["horizons"][60]["pending"] == 8  # they count as open
    # a horizon that is present is still scored next to a missing one
    both = pd.concat([forecasts(), f], ignore_index=True)
    j = fe.attach_outcomes(both, universe().drop(columns="return_60d"))
    assert j.loc[j["horizon_days"] == 20, "ret"].notna().all()


def test_gate_ok_column_limits_the_universe_but_not_the_lookup():
    uni = universe()
    extra = pd.DataFrame({"snapshot_date": [D1], "ticker": ["ZZZ"],
                          "return_20d": [0.50], "return_60d": [np.nan]})
    uni = pd.concat([uni, extra], ignore_index=True)
    uni["gate_ok"] = uni["ticker"] != "ZZZ"
    f = pd.concat([forecasts(), pd.DataFrame([{
        "session_date": D1, "ticker": "zzz", "horizon_days": 20,
        "direction": "up", "confidence": 0.6, "conviction": 3}])], ignore_index=True)
    j = fe.attach_outcomes(f, uni)
    z = j[j["ticker"] == "ZZZ"].iloc[0]
    assert z["ret"] == pytest.approx(0.50)         # requested ticker is evaluated
    assert z["uni_mean"] == pytest.approx(0.0)     # ... against the gated universe
    assert z["ex_uni"] == pytest.approx(0.50)


# ── Claude vs the shortlist: the value-add benchmark (review M3) ──────


def _hand_pack() -> pd.DataFrame:
    return pd.DataFrame({
        "session_date": [D1, D1, D1, D2],
        "ticker": ["A", "B", "D", "A"],
        "bucket": ["bull", "bull", "bear", "bull"],
    })


def test_value_add_benchmark_is_the_same_day_shortlist_of_the_called_direction():
    j = fe.attach_outcomes(forecasts(), universe(), SPY, _hand_pack())

    def row(d, t):
        return j[(j["session_date"] == d) & (j["ticker"] == t)].iloc[0]

    # D1 up call: bull shortlist {A .10, B .02}, universe mean 0 -> .06, all rose
    a1 = row(D1, "A")
    assert a1["bench_from_bucket"] and a1["bench_ex"] == pytest.approx(0.06)
    assert a1["bench_hit"] == pytest.approx(1.0)
    # D1 down calls: bear shortlist {D -.10} -> excess -.10, it fell (hit 1)
    for t in ("D", "B"):
        r = row(D1, t)
        assert r["bench_from_bucket"] and r["bench_ex"] == pytest.approx(-0.10)
        assert r["bench_hit"] == pytest.approx(1.0)
    # D2 down call: no bear shortlist that day -> the universe (0, base rate)
    c2 = row(D2, "C")
    assert not c2["bench_from_bucket"] and c2["bench_ex"] == 0.0
    assert c2["bench_hit"] == pytest.approx(c2["base_hit"])
    # neutral calls have no benchmark
    assert np.isnan(row(D1, "C")["bench_ex"])


def test_value_add_statistics_are_the_system_statistics_less_the_benchmark():
    j = fe.attach_outcomes(forecasts(), universe(), SPY, _hand_pack())
    r = fe.evaluate_direction(j, 20)
    assert r["va_available"] is True
    # up_va per session: D1 .10-.06, D2 mean(.03-.03, -.03-.03), D3 .02 - 0
    up_va = np.mean([0.04, -0.03, 0.02])
    # dn_va: D1 mean(-.10 + .10, .02 + .10), D2 (-.01 - 0)
    dn_va = np.mean([0.06, -0.01])
    assert r["va_up_minus_down"][0] == pytest.approx(up_va - dn_va)
    # hit - bench_hit per session: D1 (0 + 0 - 1) / 3, D2 0, D3 .75
    assert r["va_hit_edge"][0] == pytest.approx(np.mean([-1 / 3, 0.0, 0.75]))
    # without pack candidates the benchmark is the universe: nothing available
    plain = fe.evaluate_direction(fe.attach_outcomes(forecasts(), universe(), SPY), 20)
    assert plain["va_available"] is False
    assert plain["va_up_minus_down"][0] == pytest.approx(
        plain["up_minus_down_ex_uni"][0])


# ── skill vs no skill on a synthetic world ────────────────────────────


def _world(n_dates=140, n=60, k=24, skill=0.0, seed=0, ret_noise=0.05):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_dates)
    uni_rows, fc_rows, spy_rows, pack_rows = [], [], [], []
    for d in dates:
        shock = rng.normal(scale=0.02)
        q = rng.normal(size=n)
        ret20 = shock + skill * q + rng.normal(scale=ret_noise, size=n)
        ret60 = 2 * shock + 2 * skill * q + rng.normal(scale=ret_noise, size=n)
        tickers = [f"T{i:03d}" for i in range(n)]
        uni_rows.append(pd.DataFrame({"snapshot_date": d, "ticker": tickers,
                                      "return_20d": ret20, "return_60d": ret60}))
        spy_rows.append({"snapshot_date": d, "return_20d": shock + 0.001,
                         "return_60d": 2 * shock + 0.002})
        chosen = rng.choice(n, size=k, replace=False)
        for i in chosen:
            direction = "up" if q[i] > 0.4 else ("down" if q[i] < -0.4 else "neutral")
            conf = float(np.clip(0.5 + 0.12 * abs(q[i]), 0.5, 0.9))
            fc_rows.append({
                "session_date": d, "ticker": tickers[i], "horizon_days": 20,
                "direction": direction, "confidence": conf,
                "conviction": int(np.clip(1 + abs(q[i]) * 2, 1, 5)),
                "drivers": ["estimates", "insider"] if q[i] > 0 else ["trend"],
            })
        top = [tickers[i] for i in chosen[np.argsort(-q[chosen])][:8]]
        rnd = [tickers[i] for i in chosen[np.argsort(-q[chosen])][8:12]]
        pack_rows += [{"session_date": d, "ticker": t, "bucket": "bull"} for t in top]
        pack_rows += [{"session_date": d, "ticker": t, "bucket": "random"}
                      for t in rnd]
    return (pd.concat(uni_rows, ignore_index=True), pd.DataFrame(fc_rows),
            pd.DataFrame(spy_rows).set_index("snapshot_date"),
            pd.DataFrame(pack_rows))


@pytest.fixture(scope="module")
def skilled():
    uni, fc, spy, pack = _world(skill=0.03, seed=1)
    return uni, fc, spy, pack, fe.evaluate_forecasts(
        fc, uni, spy, pack, horizons=(20,))


@pytest.fixture(scope="module")
def unskilled():
    uni, fc, spy, pack = _world(skill=0.0, seed=2)
    return fe.evaluate_forecasts(fc, uni, spy, pack, horizons=(20,))


def test_skill_is_detected_absolute_and_relative(skilled):
    r = skilled[4]["horizons"][20]
    sel = r["selection_hit_edge"]
    assert sel[1] > 0 and r["up_selection_hit_edge"][1] > 0
    assert r["down_selection_hit_edge"][1] > 0
    assert r["hit_rate"] > r["selection_base_rate"]
    assert r["selection_hit_edge_t_nw"] > 3
    for key in ("up_minus_down_raw", "up_minus_down_ex_uni", "up_minus_down_ex_spy"):
        assert r[key][1] > 0, key
    assert r["signed_ex_uni"][1] > 0 and r["signed_ex_spy"][1] > 0
    assert r["paired_up_minus_down"][1] > 0
    ic = r["rank_ic"]
    assert ic["mean_ic"] > 0.1 and ic["t_nw"] > 3 and ic["n_dates"] == 140
    # calibration: informative confidences beat the constant-0.5 forecaster
    # and a higher confidence bin hits more often than a lower one
    assert r["brier"] < r["brier_ref"] == 0.25 and r["brier_skill"] > 0
    assert r["n_calibration"] == r["n_up"] + r["n_down"]
    hit_rates = r["calibration"]["hit_rate"].tolist()
    assert hit_rates == sorted(hit_rates) and hit_rates[-1] > hit_rates[0] + 0.2
    # the raw hit rate of the up calls is well above one half
    assert r["up_hit"][0] > 0.6


def test_no_skill_gives_no_false_positive(unskilled):
    r = unskilled["horizons"][20]
    assert abs(r["selection_hit_edge_t_nw"]) < 3 and abs(r["rank_ic"]["t_nw"]) < 3
    assert abs(r["up_minus_down_ex_uni"][0]) < 0.01
    assert abs(r["signed_ex_uni"][0]) < 0.01
    lo, hi = r["selection_hit_edge"][1:]
    assert lo < 0 < hi


def test_rank_ic_matches_scipy_per_date(skilled):
    uni, fc, spy, _, _ = skilled
    j = fe.attach_outcomes(fc, uni, spy)
    sub = j[j["ret"].notna()]
    per_date = [spearmanr(g["signed_conviction"], g["ret"])[0]
                for _, g in sub.groupby("session_date") if len(g) >= fe.MIN_IC_OBS]
    r = fe.evaluate_direction(j, 20)
    assert r["rank_ic"]["mean_ic"] == pytest.approx(np.mean(per_date), abs=1e-9)


def test_comparison_with_shortlist_random_controls_and_universe(skilled):
    comp = skilled[4]["horizons"][20]["comparison"].set_index("group")
    assert {"Claude hoch", "Claude runter", "Shortlist bullisch", "Zufalls-Kontrollen",
            "Universum", "Claude hoch minus Shortlist bullisch"} <= set(comp.index)
    assert comp.loc["Universum", "ex_uni"] == pytest.approx(0.0, abs=1e-12)
    assert comp.loc["Claude hoch", "ex_uni"] > 0 > comp.loc["Claude runter", "ex_uni"]
    assert comp.loc["Shortlist bullisch", "ex_uni"] > comp.loc[
        "Zufalls-Kontrollen", "ex_uni"]
    assert "ex_spy" in comp.columns
    assert (comp["n_ci"] == 140).all()  # dates behind each interval
    # shortlist = the 8 best signals of the day, Claude's up set (q > 0.4) is
    # about as large and equally informed: the paired difference is near zero
    diff = comp.loc["Claude hoch minus Shortlist bullisch"]
    assert abs(diff["ex_uni"]) < 0.01 and diff["n_dates"] == 140


def test_by_driver_table(skilled):
    drv = skilled[4]["horizons"][20]["by_driver"].set_index("driver")
    assert {"estimates", "insider", "trend"} <= set(drv.index)
    assert drv.loc["estimates", "n"] == drv.loc["insider", "n"]
    # "estimates" is only named for q > 0, i.e. mostly up calls that mostly hit
    assert drv.loc["estimates", "hit_rate"] > 0.5
    assert "selection_hit_edge" in drv.columns
    assert fe.by_driver(skilled[1].drop(columns="drivers").assign(
        hit=1.0), 20).empty


# ── late forecasts, report ────────────────────────────────────────────


def test_late_forecasts_are_reported_separately():
    uni, fc, spy, _ = _world(n_dates=40, skill=0.03, seed=3)
    fc = fc.assign(on_time=True)
    fc.loc[fc["session_date"] > fc["session_date"].unique()[19], "on_time"] = False
    res = fe.evaluate_forecasts(fc, uni, spy, horizons=(20,))
    assert res["n_late"] > 0 and res["n_on_time"] + res["n_late"] == res["n_forecasts"]
    assert res["horizons"][20]["n_dates"] == 20  # only the on-time sessions
    assert 20 in res["late"] and res["late"][20]["n_dates"] == 20


def test_report_is_german_markdown_and_flags_descriptive_results(skilled):
    text = fe.render_report(skilled[4], date(2026, 10, 17))
    assert text.startswith("# Prognose-Auswertung")
    for needle in ("Horizont 20 Handelstage", "Nur beschreibend", "Hoch minus Runter",
                   "Rang-IC", "Kalibrierung", "Konfidenz-Bin",
                   "Claude gegen Shortlist", "Nach Treibern", "Batch-Jackknife",
                   "Entscheidungskriterium 20 Tage", "Noch kein Urteil",
                   "Auswahl-Trefferquote (querschnittlich",
                   "konstant 0,5", "gepoolte"):
        assert needle in text, needle
    empty = fe.render_report(
        fe.evaluate_forecasts(forecasts().iloc[:0], universe()), date(2026, 10, 17))
    assert "Noch keine `forecasts.yaml`" in empty


# ── verdict markers need enough data per metric (review N3) ───────────


def test_markers_only_from_ten_horizons_of_sessions_with_data():
    uni, fc, spy, _ = _world(n_dates=210, skill=0.03, seed=9)
    long = fe.evaluate_direction(fe.attach_outcomes(fc, uni, spy), 20)
    assert long["n_obs"]["up_minus_down_ex_uni"] == 210
    assert "✅" in fe._fmt(long, "up_minus_down_ex_uni", 20)
    short_j = fe.attach_outcomes(fc[fc["session_date"] <= pd.bdate_range(
        "2023-01-02", periods=210)[149]], uni, spy)
    short = fe.evaluate_direction(short_j, 20)
    cell = fe._fmt(short, "up_minus_down_ex_uni", 20)
    assert short["n_obs"]["up_minus_down_ex_uni"] == 150
    assert "[" in cell and "✅" not in cell and "❌" not in cell and "n. s." not in cell
    # the count is per metric: a thin down side limits the difference
    thin = fc[(fc["direction"] != "down") | (fc["session_date"]
                                             <= pd.bdate_range("2023-01-02",
                                                               periods=210)[59])]
    r = fe.evaluate_direction(fe.attach_outcomes(thin, uni, spy), 20)
    assert r["n_obs"]["up_raw"] == 210 and r["n_obs"]["down_raw"] == 60
    assert r["n_obs"]["up_minus_down_ex_uni"] == 60
    assert "✅" not in fe._fmt(r, "up_minus_down_ex_uni", 20)


# ── the decisive criterion: freeze -> verdict ─────────────────────────


def _crit(fc, uni, spy, **kw):
    kw.setdefault("min_dates", 120)
    kw.setdefault("min_per_side", 100)
    return fe.primary_criterion(fc, uni, spy, **kw)


def test_freeze_waits_for_enough_sessions_and_both_sides():
    uni, fc, spy, _ = _world(n_dates=140, skill=0.03, seed=4)
    early = fe.freeze_sample(fc)  # 200 sessions needed, 140 exist
    assert early["status"] == "insufficient_data"
    assert early["progress"]["dates"] == 140
    assert early["progress"]["dates_needed"] == 200
    few = fe.freeze_sample(fc, min_dates=100, min_per_side=10_000)
    assert few["status"] == "insufficient_data"
    verdict = fe.primary_criterion(fc, uni, spy)
    assert verdict["status"] == "insufficient_data"
    assert verdict["registered"] is True and verdict["decisive_mode"] == "system"


def test_freeze_uses_only_the_forecasts_never_a_return():
    uni, fc, spy, _ = _world(n_dates=140, skill=0.03, seed=5)
    frozen = fe.freeze_sample(fc, min_dates=120, min_per_side=100)
    assert frozen["status"] == "frozen" and frozen["n_dates"] == 120
    days = pd.bdate_range("2023-01-02", periods=140)
    assert frozen["last_date"] == days[119].date()
    assert frozen["sessions"] == [d.date().isoformat() for d in days[:120]]
    assert frozen["up"] >= 100 and frozen["down"] >= 100
    assert len(frozen["forecast_ids"]) == frozen["up"] + frozen["down"]
    assert frozen["key"] == fe.sample_key(frozen)
    # later sessions do not move the sample ...
    cut = fe.freeze_sample(fc[fc["session_date"] <= days[124]], min_dates=120,
                           min_per_side=100)
    assert cut["key"] == frozen["key"]
    # ... and it needs no universe_returns at all (the signature has none)
    assert not {"universe_returns", "spy_returns"} & set(
        inspect.signature(fe.freeze_sample).parameters)
    # changing one forecast changes the key
    changed = fc.copy()
    changed.loc[changed.index[0], "ticker"] = "T999"
    assert fe.freeze_sample(changed, min_dates=120, min_per_side=100)["key"] != frozen[
        "key"]


def test_criterion_extends_the_sample_until_both_sides_are_filled():
    uni, fc, spy, _ = _world(n_dates=100, skill=0.03, seed=7)
    frozen = fe.freeze_sample(fc, min_dates=20, min_per_side=250)
    j = fe.attach_outcomes(fc, uni, spy)
    cum_up = (j[j["sigma"] > 0].groupby("session_date").size().cumsum())
    cum_dn = (j[j["sigma"] < 0].groupby("session_date").size().cumsum())
    first = max(cum_up[cum_up >= 250].index[0], cum_dn[cum_dn >= 250].index[0])
    assert frozen["last_date"] == first.date()
    assert frozen["n_dates"] > 20


def test_criterion_passes_with_skill_and_fails_without():
    uni, fc, spy, _ = _world(n_dates=140, skill=0.03, seed=5)
    good = _crit(fc, uni, spy)
    assert good["status"] == "passed"
    assert good["frozen"]["n_dates"] == 120
    assert all(c["passed"] for c in good["checks"].values())
    assert set(good["checks"]) == {"relative_up_minus_down", "selection_hit_edge"}
    assert good["modes"]["claude_value_add"]["status"] == "not_available"
    uni0, fc0, spy0, _ = _world(n_dates=140, skill=0.0, seed=6)
    bad = _crit(fc0, uni0, spy0)
    assert bad["status"] == "failed"
    assert not all(c["passed"] for c in bad["checks"].values())


def test_late_forecasts_inside_the_frozen_window_do_not_change_the_verdict():
    uni, fc, spy, _ = _world(n_dates=140, skill=0.03, seed=5)
    base = _crit(fc, uni, spy)
    assert base["status"] == "passed"
    # adversarial late forecasts on the SAME sessions and tickers' opposites:
    # counted, they would destroy the skill
    late = fc[fc["direction"] != "neutral"].sample(frac=0.5, random_state=1).copy()
    late["direction"] = late["direction"].map({"up": "down", "down": "up"})
    late["ticker"] = late["ticker"].str.replace("T", "L", regex=False)
    uni_late = uni.assign(ticker=uni["ticker"].str.replace("T", "L", regex=False))
    uni_all = pd.concat([uni, uni_late], ignore_index=True)
    # a late forecast of the same ticker would collide on (session, ticker, horizon)
    # in the natural key; give them their own tickers by renaming and mirroring
    both = pd.concat([fc.assign(on_time=True), late.assign(on_time=False)],
                     ignore_index=True)
    with_late = _crit(both, uni_all, spy)
    assert with_late["status"] == "passed"
    assert with_late["frozen"]["key"] == base["frozen"]["key"]
    assert with_late["checks"] == base["checks"]
    # the same late rows flagged on time WOULD change the sample and the numbers
    counted = _crit(both.assign(on_time=True), uni_all, spy)
    assert counted["frozen"]["key"] != base["frozen"]["key"]
    assert counted["checks"] != base["checks"]


def test_verdict_waits_for_returns_then_uses_the_grace_period():
    uni, fc, spy, _ = _world(n_dates=150, skill=0.03, seed=5)
    days = pd.bdate_range("2023-01-02", periods=150)
    frozen = fe.freeze_sample(fc, min_dates=120, min_per_side=100)
    assert frozen["last_date"] == days[119].date()
    # the windows of the last five frozen sessions have not closed yet
    gap = uni.copy()
    gap.loc[gap["snapshot_date"].isin(days[115:120]), "return_20d"] = np.nan
    joined = fe.attach_outcomes(fc, gap, spy)
    waiting = fe.verdict_from_frozen(frozen, joined, elapsed_sessions=5)
    assert waiting["status"] == "awaiting_returns"
    assert waiting["realised"] < waiting["total"] == len(frozen["forecast_ids"])
    assert waiting["deadline_sessions"] == 20 + fe.CRITERION_GRACE_SESSIONS
    assert "checks" in waiting and waiting["checks"] == {}
    assert fe.verdict_from_frozen(frozen, joined, elapsed_sessions=None)[
        "status"] == "awaiting_returns"
    # after horizon + grace sessions the verdict uses what exists
    done = fe.verdict_from_frozen(frozen, joined, elapsed_sessions=30)
    assert done["status"] == "passed" and done["n_missing"] > 0
    assert done["n_sessions_used"] == 115
    # sessions_after counts the sessions that came after the last frozen one
    assert fe.sessions_after(gap, days[119]) == 30
    live = fe.primary_criterion(fc, gap, spy, min_dates=120, min_per_side=100)
    assert live["status"] == "passed" and live["n_missing"] > 0
    young = fe.primary_criterion(fc[fc["session_date"] <= days[139]],
                                 gap[gap["snapshot_date"] <= days[139]], spy,
                                 min_dates=120, min_per_side=100)
    assert young["status"] == "awaiting_returns"  # only 20 sessions elapsed


def test_a_frozen_verdict_is_not_changed_by_later_corrections():
    uni, fc, spy, _ = _world(n_dates=140, skill=0.03, seed=5)
    frozen = fe.freeze_sample(fc, min_dates=120, min_per_side=100)
    first = fe.verdict_from_frozen(
        frozen, fe.attach_outcomes(fc, uni, spy), elapsed_sessions=20)
    assert first["status"] == "passed" and len(first["values"]) == len(
        frozen["forecast_ids"])
    # later a price correction reverses early returns of the frozen window ...
    days = pd.bdate_range("2023-01-02", periods=140)
    revised = uni.copy()
    early = revised["snapshot_date"].isin(days[:60])
    revised.loc[early, "return_20d"] = -revised.loc[early, "return_20d"]
    recomputed = fe.verdict_from_frozen(
        frozen, fe.attach_outcomes(fc, revised, spy), elapsed_sessions=20)
    assert recomputed["checks"] != first["checks"]  # live data moved
    # ... the persisted verdict is recomputed from its frozen values: unchanged
    again = fe.verdict_from_values(frozen, pd.DataFrame(first["values"]))
    assert again["checks"] == first["checks"] and again["status"] == first["status"]
    assert again["values"] == first["values"]
    assert set(first["values"][0]) >= {"forecast_id", "session_date", "ret",
                                       "ex_uni", "hit", "base_hit", "bench_ex"}


def test_a_verdict_given_to_the_evaluation_is_reported_as_it_is():
    uni, fc, spy, pack = _world(n_dates=140, skill=0.03, seed=5)
    frozen = fe.freeze_sample(fc, min_dates=120, min_per_side=100)
    stored = fe.verdict_from_frozen(
        frozen, fe.attach_outcomes(fc, uni, spy), elapsed_sessions=20)
    res = fe.evaluate_forecasts(fc, uni, spy, pack, horizons=(20,),
                                verdicts={20: stored})
    assert res["criterion"][20]["frozen"]["key"] == frozen["key"]
    assert res["criterion"][20]["checks"] == stored["checks"]
    text = fe.render_report(res, date(2026, 10, 17))
    assert "**Urteil: bestanden**" in text and frozen["key"][:12] in text
    assert "eingefrorene Stichprobe: 120 Sitzungen" in text
    # a frozen sample passed in is used instead of freezing again
    res2 = fe.evaluate_forecasts(fc, uni, spy, pack, horizons=(20,),
                                 frozen={20: frozen})
    assert res2["criterion"][20]["frozen"]["key"] == frozen["key"]


# ── system vs claude_value_add (review M3) ────────────────────────────


def test_both_modes_are_computed_and_system_is_the_default():
    uni, fc, spy, pack = _world(n_dates=140, skill=0.03, seed=5)
    v = _crit(fc, uni, spy, pack_candidates=pack)
    assert v["decisive_mode"] == "system" == fe.DEFAULT_CRITERION_MODE
    assert set(v["modes"]) == {"system", "claude_value_add"}
    assert v["modes"]["system"]["status"] == "passed"
    va = v["modes"]["claude_value_add"]
    assert va["status"] in ("passed", "failed")  # available with pack candidates
    # the shortlist (8 best signals) is about as good as Claude's up calls, so
    # the up side adds ~nothing; the world has no bear shortlist, so the down
    # side is judged against the universe, like in the system mode
    sys_rel = v["modes"]["system"]["checks"]["relative_up_minus_down"]["estimate"]
    va_rel = va["checks"]["relative_up_minus_down"]["estimate"]
    assert 0 < va_rel < sys_rel
    # the decisive mode is configurable and switches the verdict, not the sample
    other = _crit(fc, uni, spy, pack_candidates=pack, decisive_mode="claude_value_add")
    assert other["decisive_mode"] == "claude_value_add"
    assert other["status"] == va["status"]
    assert other["frozen"]["key"] == v["frozen"]["key"]
    assert other["checks"] == va["checks"]
    with pytest.raises(ValueError, match="criterion mode"):
        _crit(fc, uni, spy, decisive_mode="absolute")
    text = fe.render_report(
        fe.evaluate_forecasts(fc, uni, spy, pack, horizons=(20,),
                              frozen={20: fe.freeze_sample(fc, min_dates=120,
                                                           min_per_side=100)}),
        date(2026, 10, 17))
    assert "System: Claudes Prognosen gegen das Gate-Universum" in text
    assert "Mehrwert: Claudes Prognosen gegen die Shortlist" in text


def test_value_add_without_pack_candidates_is_not_available():
    uni, fc, spy, _ = _world(n_dates=140, skill=0.03, seed=5)
    v = _crit(fc, uni, spy, decisive_mode="claude_value_add")
    assert v["status"] == "not_available"
    assert v["modes"]["system"]["status"] == "passed"


# ── forecaster version: a change starts a new frozen sample (review M6) ──


def test_a_new_forecaster_version_starts_a_new_sample():
    uni, fc, spy, _ = _world(n_dates=140, skill=0.03, seed=5)
    days = pd.bdate_range("2023-01-02", periods=140)
    versioned = fc.assign(forecaster_version=np.where(
        fc["session_date"] <= days[69], "skill-v1", "skill-v2"))
    # both versions would fill 60 sessions: the active one is the newest
    s = fe.freeze_sample(versioned, min_dates=60, min_per_side=50)
    assert s["forecaster_version"] == "skill-v2" and s["status"] == "frozen"
    assert s["first_date"] == days[70].date()
    assert all(i >= days[70].date().isoformat() for i in s["sessions"])
    assert [o["forecaster_version"] for o in s["earlier_versions"]] == ["skill-v1"]
    assert s["earlier_versions"][0]["status"] == "frozen"
    # the older version is still available on request, independent of the new one
    old = fe.freeze_sample(versioned, min_dates=60, min_per_side=50,
                           forecaster_version="skill-v1")
    assert old["last_date"] == days[59].date()
    assert old["forecaster_version"] == "skill-v1"
    # a change BEFORE the sample is full aborts the partial sample
    big = fe.freeze_sample(versioned, min_dates=100, min_per_side=50)
    assert big["forecaster_version"] == "skill-v2"
    assert big["status"] == "insufficient_data"
    assert big["progress"]["dates"] == 70
    aborted = big["earlier_versions"][0]
    assert aborted["forecaster_version"] == "skill-v1"
    assert aborted["status"] == "aborted" and aborted["progress"]["dates"] == 70
    # the criterion filters on the active version only
    v = fe.primary_criterion(versioned, uni, spy, min_dates=60, min_per_side=50)
    assert v["forecaster_version"] == "skill-v2"
    assert v["frozen"]["first_date"] == days[70].date()
    text = fe.render_report(fe.evaluate_forecasts(
        versioned, uni, spy, horizons=(20,)), date(2026, 10, 17))
    assert "abgebrochene Teilstichprobe" in text and "`skill-v1`" in text


def test_missing_versions_are_one_unversioned_stream():
    uni, fc, spy, _ = _world(n_dates=140, skill=0.03, seed=5)
    s = fe.freeze_sample(fc, min_dates=120, min_per_side=100)
    assert s["forecaster_version"] == fe.UNVERSIONED and s["earlier_versions"] == []


# ── only the 20-day criterion is registered (review N2) ───────────────


def test_the_sixty_day_criterion_is_labelled_not_registered():
    uni, fc, spy, _ = _world(n_dates=140, skill=0.03, seed=5)
    fc60 = fc.assign(horizon_days=60)
    res = fe.evaluate_forecasts(pd.concat([fc, fc60], ignore_index=True), uni, spy,
                                horizons=(20, 60))
    assert res["criterion"][20]["registered"] is True
    assert res["criterion"][60]["registered"] is False
    text = fe.render_report(res, date(2026, 10, 17))
    assert "Entscheidungskriterium 20 Tage (vorab registriert, Vorschlag)" in text
    assert "Entscheidungskriterium 60 Tage (nicht registriert" in text
