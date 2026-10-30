"""Unit tests for the shared event-study building blocks.

Alignment, de-duplication and the join with the return rows. The evaluation
itself (horizon returns, excess, intervals, decision rule) and the CLI script
are covered in ``test_event_study_horizon.py``.
"""

import pandas as pd
import pytest

from trading_signals.analysis.event_study import (
    align_events,
    dedupe_events,
    evaluate_events_horizon,
    event_selection,
    horizon_meta,
)

SESSIONS = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-08"])


def _returns():
    """Three tickers on four sessions; DDD has no realised return."""
    rows = []
    for d in SESSIONS:
        for t, r in [("AAA", 0.05), ("BBB", -0.03), ("CCC", 0.01)]:
            rows.append({"snapshot_date": d, "ticker": t, "return_20d": r})
    rows.append({"snapshot_date": SESSIONS[0], "ticker": "DDD", "return_20d": None})
    return pd.DataFrame(rows)


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


def test_event_selection_drops_duplicates_and_keeps_extra_columns():
    meta = horizon_meta(_returns(), (20,))
    assert "DDD" in set(meta["ticker"])  # unrealised rows stay in the frame ...
    ev = pd.DataFrame({"ticker": ["AAA", "AAA", "BBB"],
                       "event_date": ["2024-01-02", "2024-01-02", "2024-01-04"],
                       "with_earnings": [True, True, False]})
    sel = event_selection(align_events(ev, SESSIONS), meta)
    assert len(sel) == 2 and "with_earnings" in sel.columns  # duplicate dropped
    stats = evaluate_events_horizon(sel, meta, 20, bootstrap=False)
    # ... but the evaluation only counts realised returns
    assert stats["n_events"] == 2 and stats["days_with"] == 2
    assert stats["n_unrealised"] == 0
    assert stats["mean_ret"] == pytest.approx((0.05 - 0.03) / 2)
    assert stats["uni_mean"] == pytest.approx((0.05 - 0.03 + 0.01) / 3)
    assert stats["hit_rate"] == pytest.approx(0.5)  # AAA up, BBB down
    assert stats["uni_hit_rate"] == pytest.approx(2 / 3)
