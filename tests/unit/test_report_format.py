"""Unit tests for analysis.report_format and the "no CI without two batches" rule."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tests.unit.forecast_data import forecasts, universe
from trading_signals.analysis import forecast_eval as fe
from trading_signals.analysis.event_study import (
    align_events,
    evaluate_events_horizon,
    event_selection,
    horizon_meta,
    passes_criterion_horizon,
)
from trading_signals.analysis.report_format import DASH, ci_cell, num, pct


def test_pct_and_num():
    assert pct(0.0123) == "+1.23 %" and pct(-0.0123, 1) == "-1.2 %"
    assert pct(0.5, 1, signed=False) == "50.0 %"
    assert pct(float("nan")) == DASH and pct(None) == DASH and pct("x") == DASH
    assert num(1.23456, 3) == "1.235" and num(float("nan")) == DASH
    assert num(None) == DASH


def test_ci_cell_markers():
    assert ci_cell(0.02, 0.01, 0.03).endswith("✅")
    assert ci_cell(-0.02, -0.03, -0.01).endswith("❌")
    assert ci_cell(0.0, -0.01, 0.01).endswith("n. s.")
    assert ci_cell(0.02, 0.01, 0.03) == "+2.00 % [+1.00 % … +3.00 %] ✅"
    assert ci_cell(float("nan"), 0.0, 0.0) == DASH
    assert ci_cell(None, None, None) == DASH


def test_ci_cell_without_an_interval_shows_the_bare_estimate():
    assert ci_cell(0.02, float("nan"), float("nan")) == "+2.00 % (KI zu wenig Daten)"
    assert ci_cell(0.02, None, None) == "+2.00 %"


def test_ci_cell_without_marker_and_unsigned():
    # too little data for a verdict: the interval, but no check / cross / n. s.
    assert ci_cell(0.02, 0.01, 0.03, mark=False) == "+2.00 % [+1.00 % … +3.00 %]"
    assert ci_cell(0.0, -0.01, 0.01, mark=False) == "+0.00 % [-1.00 % … +1.00 %]"
    assert ci_cell(0.55, 0.50, 0.60, 1, mark=False, signed=False) == (
        "55.0 % [50.0 % … 60.0 %]")
    assert ci_cell(0.55, 0.50, 0.60, 1, signed=False).endswith("✅")
    assert ci_cell(0.02, float("nan"), float("nan"), mark=False) == (
        "+2.00 % (KI zu wenig Daten)")


def test_too_few_dates_for_a_jackknife_give_no_interval():
    # forecast evaluation: 3 sessions, batches of 2 x 20 sessions
    joined = fe.attach_outcomes(forecasts(), universe())
    r = fe.evaluate_direction(joined, 20)
    est, lo, hi = r["selection_hit_edge"]
    assert np.isfinite(est) and np.isnan(lo) and np.isnan(hi)
    assert np.isnan(r["paired_up_minus_down"][1])
    assert np.isfinite(r["paired_up_minus_down"][0])
    # event study: the same rule, so the decision rule cannot pass
    dates = pd.to_datetime(["2024-03-01", "2024-03-04", "2024-03-05"])
    meta = horizon_meta(universe(), (20,))
    ev = pd.DataFrame({"ticker": ["A", "B"], "event_date": ["2024-03-01"] * 2})
    sel = event_selection(align_events(ev, dates), meta)
    s = evaluate_events_horizon(sel, meta, 20)
    assert "diff_lo" not in s and np.isfinite(s["diff"])
    assert not passes_criterion_horizon({**s, "n_events": 500})


def test_comparison_table_without_interval_still_has_estimates():
    pack = pd.DataFrame({"session_date": ["2024-03-01"] * 2, "ticker": ["A", "D"],
                         "bucket": ["bull", "random"]})
    joined = fe.attach_outcomes(forecasts(), universe())
    comp = fe.compare_groups(joined, universe(), pack, 20)
    claude = comp.set_index("group").loc["Claude hoch"]
    assert np.isfinite(claude["ex_uni"]) and np.isnan(claude["ex_uni_lo"])
    with pytest.raises(KeyError):
        comp.set_index("group").loc["Wunsch-Ticker"]
