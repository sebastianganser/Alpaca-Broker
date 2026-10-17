"""Event study, horizon mode (strategy-neutral, plan 2026-10-17 step 2).

Synthetic frames only; the CLI script is exercised with mocked loaders (no DB).
The barrier mode keeps its tests in ``test_event_study.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from trading_signals.analysis.event_study import (
    align_events,
    dedupe_events,
    evaluate_events_horizon,
    event_selection,
    horizon_meta,
    passes_criterion_horizon,
)

DATES = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
ROOT = Path(__file__).resolve().parents[2]


def _returns() -> pd.DataFrame:
    """3 sessions x 4 tickers; the last session has no realised 60d return."""
    r20 = {
        DATES[0]: {"A": 0.10, "B": 0.02, "C": -0.02, "D": -0.10},   # mean 0
        DATES[1]: {"A": 0.08, "B": 0.06, "C": 0.04, "D": 0.02},     # mean .05
        DATES[2]: {"A": 0.00, "B": 0.00, "C": 0.04, "D": 0.04},     # mean .02
    }
    rows = []
    for d, rets in r20.items():
        for t, r in rets.items():
            rows.append({"snapshot_date": d, "ticker": t, "return_20d": r,
                         "return_60d": np.nan if d == DATES[2] else 2 * r})
    return pd.DataFrame(rows)


def _spy() -> pd.DataFrame:
    return pd.DataFrame({"return_20d": [0.01, 0.03, 0.0],
                         "return_60d": [0.02, 0.06, 0.0]}, index=DATES)


def test_horizon_meta_excess_over_the_session_mean_and_spy():
    m = horizon_meta(_returns(), (20, 60), _spy())
    a0 = m[(m["snapshot_date"] == DATES[0]) & (m["ticker"] == "A")].iloc[0]
    assert a0["ret_20"] == pytest.approx(0.10)
    assert a0["ex_20"] == pytest.approx(0.10)
    assert a0["spy_20"] == pytest.approx(0.01)
    assert a0["exspy_20"] == pytest.approx(0.09)
    assert a0["ret_60"] == pytest.approx(0.20)
    assert a0["exspy_60"] == pytest.approx(0.18)
    d2 = m[m["snapshot_date"] == DATES[1]]
    assert d2["ex_20"].sum() == pytest.approx(0.0)  # demeaned per session
    open_window = m[m["snapshot_date"] == DATES[2]]
    assert open_window["ret_60"].isna().all() and open_window["ex_60"].isna().all()
    # without SPY the SPY columns are absent; a frame with a date column works too
    assert "spy_20" not in horizon_meta(_returns(), (20,)).columns
    again = horizon_meta(_returns(), (20,), _spy().reset_index(names="snapshot_date"))
    assert again["spy_20"].notna().all()


def test_evaluate_events_horizon_raw_excess_and_hit_rates():
    meta = horizon_meta(_returns(), (20, 60), _spy())
    ev = pd.DataFrame({
        "ticker": ["A", "D", "B", "C"],
        "event_date": ["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-04"],
    })
    sel = event_selection(align_events(ev, DATES), meta)
    s = evaluate_events_horizon(sel, meta, 20)
    # per event date: D0 mean(.10, -.10) = 0 ; D1 B .06 ; D2 C .04
    assert (s["n_events"], s["days_with"], s["n_unrealised"]) == (4, 3, 0)
    assert s["mean_ret"] == pytest.approx(np.mean([0.0, 0.06, 0.04]))
    assert s["uni_mean"] == pytest.approx(np.mean([0.0, 0.05, 0.02]))
    assert s["diff"] == pytest.approx(s["mean_ret"] - s["uni_mean"])
    # three event dates hold no jackknife batch: an estimate, no interval
    assert "diff_lo" not in s and "diff_spy_lo" not in s
    # hits: D0 1 of 2 (A up, D down), D1 1 of 1, D2 1 of 1
    assert s["event_hit_rate"] == pytest.approx(3 / 4)
    assert s["hit_rate"] == pytest.approx(np.mean([0.5, 1.0, 1.0]))
    assert s["uni_hit_rate"] == pytest.approx(np.mean([0.5, 1.0, 0.5]))
    assert s["hit_edge"] == pytest.approx(s["hit_rate"] - s["uni_hit_rate"])
    # SPY-relative: SPY returns .01 / .03 / 0 on the three event dates
    assert s["spy_mean"] == pytest.approx(np.mean([0.01, 0.03, 0.0]))
    assert s["diff_spy"] == pytest.approx(s["mean_ret"] - s["spy_mean"])
    # event-weighted excess over the session mean: .10, -.10, .06-.05, .04-.02
    assert s["event_ex_mean"] == pytest.approx(np.mean([0.10, -0.10, 0.01, 0.02]))


def test_evaluate_events_horizon_unrealised_and_empty():
    meta = horizon_meta(_returns(), (20, 60))
    ev = pd.DataFrame({"ticker": ["A", "B"],
                       "event_date": ["2024-01-02", "2024-01-04"]})
    sel = event_selection(align_events(ev, DATES), meta)
    s60 = evaluate_events_horizon(sel, meta, 60, bootstrap=False)
    # the 2024-01-04 event has no 60-session return yet
    assert s60["n_events"] == 1 and s60["n_unrealised"] == 1
    assert "diff_lo" not in s60 and "spy_mean" not in s60
    assert s60["diff"] == pytest.approx(0.20 - 0.0)  # D0 universe 60d mean is 0
    empty = evaluate_events_horizon(sel.iloc[:0], meta, 20)
    assert empty["n_events"] == 0 and empty["days_with"] == 0
    assert not passes_criterion_horizon(empty)


def _universe(effect: float, n_dates=160, n=40, seed=0):
    """Universe with two events per date; events earn ``effect`` extra."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_dates)
    rows, events = [], []
    for i, d in enumerate(dates):
        shock = rng.normal(scale=0.03)
        for k in range(n):
            event = k < 2
            r20 = shock + rng.normal(scale=0.05) + (effect if event else 0.0)
            rows.append({"snapshot_date": d, "ticker": f"T{k:02d}",
                         "return_20d": r20, "return_60d": 2 * r20})
            if event:
                events.append({"ticker": f"T{k:02d}", "event_date": d,
                               "grp": "x" if i % 2 else "y"})
    return pd.DataFrame(rows), pd.DataFrame(events), dates


def test_planted_event_effect_is_found_and_passes_the_criterion():
    ret, ev, dates = _universe(effect=0.03)
    meta = horizon_meta(ret, (20, 60))
    sel = event_selection(align_events(ev, dates), meta)
    s = evaluate_events_horizon(sel, meta, 20)
    assert s["n_events"] == 320 and s["diff"] == pytest.approx(0.03, abs=0.01)
    assert s["diff_lo"] > 0 and s["hit_edge_lo"] > 0  # 160 dates = 4 batches of 40
    assert s["diff_lo"] < s["diff"] < s["diff_hi"]
    assert passes_criterion_horizon(s)
    # the 60-session returns double the effect
    # (160 dates hold only one 120-date batch at 60 sessions: no interval)
    long = evaluate_events_horizon(sel, meta, 60)
    assert long["diff"] > s["diff"] and "diff_lo" not in long


def test_null_effect_does_not_pass_and_small_samples_never_do():
    ret, ev, dates = _universe(effect=0.0, seed=1)
    meta = horizon_meta(ret, (20, 60))
    sel = event_selection(align_events(ev, dates), meta)
    s = evaluate_events_horizon(sel, meta, 20)
    assert s["diff_lo"] < 0 < s["diff_hi"]
    assert not passes_criterion_horizon(s)
    good = {"n_events": 150, "mean_ret": 0.01, "diff_lo": 0.001}
    assert passes_criterion_horizon(good)
    assert not passes_criterion_horizon({**good, "n_events": 149})
    assert not passes_criterion_horizon({**good, "mean_ret": -0.01})
    assert not passes_criterion_horizon({**good, "diff_lo": -0.001})
    assert not passes_criterion_horizon({})


def test_delay_shifts_the_start_session():
    meta = horizon_meta(_returns(), (20,))
    ev = pd.DataFrame({"ticker": ["A"], "event_date": ["2024-01-02"]})
    now = evaluate_events_horizon(event_selection(align_events(ev, DATES), meta),
                                  meta, 20, bootstrap=False)
    later = evaluate_events_horizon(
        event_selection(align_events(ev, DATES, delay=1), meta), meta, 20,
        bootstrap=False)
    assert now["mean_ret"] == pytest.approx(0.10)
    assert later["mean_ret"] == pytest.approx(0.08)  # A on the next session


# ── the CLI script (both sources) with mocked loaders ─────────────────


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "event_study_script", ROOT / "scripts" / "analysis" / "event_study.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["event_study_script"] = mod
    spec.loader.exec_module(mod)
    return mod


@contextmanager
def _no_db():
    yield MagicMock()


@pytest.fixture(scope="module")
def script():
    return _load_script()


def _patches(script, ret, dates, events):
    spy = pd.DataFrame({"return_20d": 0.002, "return_60d": 0.004}, index=dates)
    return [
        patch("trading_signals.db.session.get_session", _no_db),
        patch.object(script, "load_returns", return_value=ret),
        patch.object(script, "load_sessions", return_value=list(dates)),
        patch.object(script, "load_events", return_value=events),
        patch("trading_signals.analysis.benchmark.load_benchmark_returns",
              return_value=spy),
    ]


def _run(script, ret, dates, events, argv):
    ps = _patches(script, ret, dates, events)
    for p in ps:
        p.start()
    try:
        return script.main(argv)
    finally:
        for p in reversed(ps):
            p.stop()


def test_script_horizon_mode_buyback_source(script, tmp_path):
    ret, ev, dates = _universe(effect=0.03, seed=2)
    ev["event_date"] = ev["event_date"].dt.date.astype(str)
    argv = ["--source", "buyback", "--events-csv", "unused.csv", "--min-gap-days", "0",
            "--start", "2023-01-01", "--out", str(tmp_path)]
    assert _run(script, ret, dates, ev, argv) == 0
    reports = list(tmp_path.glob("event_study_buyback_horizon_2*.md"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    for needle in ("Prognose-Horizonte", "Horizont 20 Handelstage",
                   "Horizont 60 Handelstage", "Überschuss vs. SPY",
                   "Verspäteter Einstieg (20 Tage)", "Je Jahr (60 Tage)",
                   "Entscheidungsregel", "✅ bestanden"):
        assert needle in text, needle
    assert "320 " in text  # all events kept without the dedupe gap
    assert list(tmp_path.glob("event_study_buyback_horizon_trades_*.csv"))


def test_script_horizon_mode_index_add_source(script, tmp_path):
    ret, ev, dates = _universe(effect=0.0, seed=3)
    ev["event_date"] = ev["event_date"].dt.date.astype(str)
    argv = ["--source", "index_add", "--start", "2023-01-01", "--block", "10",
            "--out", str(tmp_path)]
    assert _run(script, ret, dates, ev, argv) == 0
    text = next(tmp_path.glob("event_study_index_add_horizon_2*.md")).read_text(
        encoding="utf-8")
    assert "Index-Aufnahmen (D2, explorativ)" in text
    assert "keine Entscheidungsregel" in text
    assert "bestanden" not in text.replace("nicht bestanden", "")


def test_script_start_defaults_to_the_ml_start_date_at_run_time(script, tmp_path):
    assert script.parse_args(["--source", "index_add"]).start is None
    ret, ev, dates = _universe(effect=0.0, n_dates=40, seed=5)
    ev["event_date"] = ev["event_date"].dt.date.astype(str)
    spy = pd.DataFrame({"return_20d": 0.0, "return_60d": 0.0}, index=dates)
    with (
        patch("trading_signals.db.session.get_session", _no_db),
        patch("trading_signals.utils.retention.ml_start_date",
              return_value=date(2023, 1, 1)),
        patch.object(script, "load_returns", return_value=ret) as loader,
        patch.object(script, "load_sessions", return_value=list(dates)),
        patch.object(script, "load_events", return_value=ev),
        patch("trading_signals.analysis.benchmark.load_benchmark_returns",
              return_value=spy),
    ):
        assert script.main(["--source", "index_add", "--out", str(tmp_path)]) == 0
    assert loader.call_args.args[1] == date(2023, 1, 1)  # not a frozen 2022-07-01


def test_script_buyback_needs_the_events_csv(script, tmp_path):
    ret, _, dates = _universe(effect=0.0, n_dates=30, seed=4)
    # load_events logs the error and returns None -> exit code 2
    ps = [
        patch("trading_signals.db.session.get_session", _no_db),
        patch.object(script, "load_returns", return_value=ret),
        patch.object(script, "load_sessions", return_value=list(dates)),
        patch("trading_signals.analysis.benchmark.load_benchmark_returns",
              return_value=pd.DataFrame({"return_20d": 0.0, "return_60d": 0.0},
                                        index=dates)),
    ]
    for p in ps:
        p.start()
    try:
        assert script.main(["--source", "buyback", "--out", str(tmp_path)]) == 2
    finally:
        for p in reversed(ps):
            p.stop()


def test_script_reads_the_buyback_csv_of_the_collector(script, tmp_path):
    csv = tmp_path / "buyback_events.csv"
    pd.DataFrame({
        "ticker": ["A", "B"], "cik": [1, 2],
        "event_date": ["2024-01-02", "2024-01-03"],
        "with_earnings": [True, False], "amount_usd": [1e9, 5e8],
    }).to_csv(csv, index=False)
    df = script.load_buybacks(str(csv))
    assert df["grp"].tolist() == ["mit Quartalszahlen", "ohne Quartalszahlen"]
    # the groups survive dedupe -> alignment -> selection
    meta = horizon_meta(_returns(), (20,))
    sel = event_selection(align_events(dedupe_events(df, 5), DATES), meta)
    assert sorted(sel["grp"]) == ["mit Quartalszahlen", "ohne Quartalszahlen"]


def test_script_barrier_mode_still_works(script, tmp_path):
    rows = []
    for d in DATES:
        for t, net, out in [("AAA", 0.0095, 1), ("BBB", -0.0205, -1)]:
            rows.append({"snapshot_date": d, "ticker": t, "return_barrier_14d": net,
                         "barrier_outcome": out, "barrier_ambiguous": False})
    labels = pd.DataFrame(rows)
    events = pd.DataFrame({"ticker": ["AAA"], "event_date": ["2024-01-03"],
                           "grp": ["sp500"]})
    ps = [
        patch("trading_signals.db.session.get_session", _no_db),
        patch.object(script, "load_labels", return_value=labels),
        patch.object(script, "load_sessions", return_value=list(DATES)),
        patch.object(script, "load_events", return_value=events),
    ]
    for p in ps:
        p.start()
    try:
        rc = script.main(["--source", "index_add", "--mode", "barrier",
                          "--start", "2024-01-01", "--block", "1",
                          "--out", str(tmp_path)])
    finally:
        for p in reversed(ps):
            p.stop()
    assert rc == 0
    text = next(tmp_path.glob("event_study_index_add_2*.md")).read_text(
        encoding="utf-8")
    assert "Trade +1 % / −2 % / 14 Handelstage" in text
    assert "Verspäteter Einstieg (Gültigkeit des Signals)" in text
