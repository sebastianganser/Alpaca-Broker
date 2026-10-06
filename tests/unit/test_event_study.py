"""Unit tests for the event-study building blocks (step D)."""

import numpy as np
import pandas as pd
import pytest

from trading_signals.analysis.event_study import (
    align_events,
    dedupe_events,
    evaluate_events,
    event_selection,
    label_meta,
    passes_criterion,
)
from trading_signals.analysis.hit_model import STOP_NET

SESSIONS = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-08"])


def _labels():
    rows = []
    for d in SESSIONS:
        for t, net, out, amb in [("AAA", 0.0095, 1, False), ("BBB", -0.0205, -1, False),
                                 ("CCC", 0.0095, 1, True)]:
            rows.append({"snapshot_date": d, "ticker": t, "return_barrier_14d": net,
                         "barrier_outcome": out, "barrier_ambiguous": amb})
    rows.append({"snapshot_date": SESSIONS[0], "ticker": "DDD", "return_barrier_14d": None,
                 "barrier_outcome": None, "barrier_ambiguous": None})
    return pd.DataFrame(rows)


def test_label_meta_conservative_rule_and_drops_unlabelled():
    meta = label_meta(_labels())
    assert len(meta) == 12 and "DDD" not in set(meta["ticker"])
    ccc = meta[meta["ticker"] == "CCC"].iloc[0]
    assert ccc["y"] == 1 and ccc["net"] == pytest.approx(0.0095)
    assert ccc["net_cons"] == pytest.approx(STOP_NET)
    aaa = meta[meta["ticker"] == "AAA"].iloc[0]
    assert aaa["net_cons"] == pytest.approx(aaa["net"])


def test_align_events_first_session_on_or_after_and_delay():
    ev = pd.DataFrame({"ticker": ["AAA", "BBB", "CCC"],
                       "event_date": ["2024-01-03", "2024-01-06", "2024-01-09"]})
    out = align_events(ev, SESSIONS)
    # weekend event -> next session; event after last session dropped
    assert list(out["ticker"]) == ["AAA", "BBB"]
    assert list(out["snapshot_date"]) == [SESSIONS[1], SESSIONS[3]]
    delayed = align_events(ev, SESSIONS, delay=1)
    assert list(delayed["ticker"]) == ["AAA"]
    assert delayed["snapshot_date"].iloc[0] == SESSIONS[2]


def test_dedupe_events_min_gap():
    ev = pd.DataFrame({
        "ticker": ["AAA", "AAA", "AAA", "BBB"],
        "event_date": ["2024-01-02", "2024-01-05", "2024-01-20", "2024-01-03"],
    })
    out = dedupe_events(ev, min_gap_days=5)
    assert list(zip(out["ticker"], out["event_date"].dt.day, strict=True)) == [
        ("AAA", 2), ("AAA", 20), ("BBB", 3)]


def test_event_selection_and_evaluation():
    meta = label_meta(_labels())
    ev = pd.DataFrame({"ticker": ["AAA", "AAA", "BBB"],
                       "event_date": ["2024-01-02", "2024-01-02", "2024-01-04"],
                       "with_earnings": [True, True, False]})
    sel = event_selection(align_events(ev, SESSIONS), meta)
    assert len(sel) == 2 and "with_earnings" in sel.columns  # duplicate dropped
    stats = evaluate_events(sel, meta, block=1, bootstrap=False)
    assert stats["n_events"] == 2 and stats["days_with"] == 2
    assert stats["trade_hit_rate"] == pytest.approx(0.5)
    uni_day = np.mean([0.0095, -0.0205, 0.0095])
    assert stats["uni_net"] == pytest.approx(uni_day)
    assert stats["net"] == pytest.approx((0.0095 - 0.0205) / 2)


def test_passes_criterion():
    good = {"n_events": 200, "net": 0.001, "diff_lo": 0.0001}
    assert passes_criterion(good)
    assert not passes_criterion({**good, "n_events": 149})
    assert not passes_criterion({**good, "net": -0.001})
    assert not passes_criterion({**good, "diff_lo": -0.0001})
    assert not passes_criterion({})
