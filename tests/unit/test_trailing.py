"""Tests for trading_signals.analysis.trailing (hand-computed OHLC paths)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_signals.analysis.trailing import (
    MAIN_TRAILING,
    TrailingParams,
    simulate_windows,
    summarize,
    trail_distance,
)

# 5-session windows, no cost: easy to follow by hand.
P = TrailingParams(atr_mult=3.0, atr_period=14, max_days=5, cost=0.0)
FLAT = (100, 100, 100, 100)


def _w(*trades):
    """trades: list of paths, each a list of (o, h, l, c) per session."""
    a = np.array(trades, dtype=float)
    return a[:, :, 0], a[:, :, 1], a[:, :, 2], a[:, :, 3]


def _run(trades, dist, p=P):
    o, h, lo, c = _w(*trades)
    return simulate_windows(o, h, lo, c, np.atleast_1d(np.asarray(dist, float)), p)


def test_main_trailing_is_the_decided_trade():
    assert MAIN_TRAILING.atr_mult == 3.0
    assert MAIN_TRAILING.atr_period == 14
    assert MAIN_TRAILING.max_days == 60
    assert MAIN_TRAILING.cost == pytest.approx(0.0005)
    assert "3" in MAIN_TRAILING.label and "60" in MAIN_TRAILING.label


def test_trail_distance_is_atr_multiple():
    assert trail_distance(2.0) == pytest.approx(6.0)
    assert trail_distance(np.array([1.0, 2.0]), P) == pytest.approx([3.0, 6.0])


def test_day_one_stop_via_low_fills_at_stop_price():
    # entry 100, D = 3 -> stop 97; day-1 low 96.5 touches it
    out = _run([[(100, 101, 96.5, 99), *[FLAT] * 4]], 3.0)
    stopped, net, day, gap = out
    assert stopped[0] and day[0] == 1 and not gap[0]
    assert net[0] == pytest.approx(97 / 100 - 1)


def test_day_one_low_above_stop_does_not_stop():
    out = _run([[(100, 101, 97.5, 99), *[FLAT] * 4]], 3.0)
    assert not out[0][0] and out[2][0] == 5


def test_gap_through_stop_on_later_day_fills_at_open():
    # day 1 high 105 -> HWM 105, stop on day 2 = 102; day 2 opens at 99 (gap)
    out = _run([[(100, 105, 100, 104), (99, 100, 98, 99.5), *[FLAT] * 3]], 3.0)
    stopped, net, day, gap = out
    assert stopped[0] and day[0] == 2 and gap[0]
    assert net[0] == pytest.approx(99 / 100 - 1)  # open, NOT the 102 stop price


def test_intraday_stop_on_later_day_fills_at_stop_not_open():
    # day 2 opens above the stop (103 > 102) and trades down through it
    out = _run([[(100, 105, 100, 104), (103, 104, 101, 101.5), *[FLAT] * 3]], 3.0)
    stopped, net, day, gap = out
    assert stopped[0] and day[0] == 2 and not gap[0]
    assert net[0] == pytest.approx(102 / 100 - 1)


def test_high_water_mark_ratchets_up_and_pullback_stops_at_hwm_minus_d():
    path = [
        (100, 104, 100, 103),     # HWM 104
        (103, 108, 103, 107),     # stop 101 not touched, HWM 108
        (107, 107.5, 104, 105),   # stop 105: low 104 touches, open 107 above
        *[FLAT] * 2,
    ]
    stopped, net, day, gap = _run([path], 3.0)
    assert stopped[0] and day[0] == 3 and not gap[0]
    assert net[0] == pytest.approx(105 / 100 - 1)  # HWM 108 - D 3, not 101


def test_stop_never_moves_down():
    # HWM 110 after day 1 lifts the stop from the initial 97 to 107: the
    # trade exits at 107 on day 2, never at the original stop.
    path = [(100, 110, 100, 109), (109, 109, 106.5, 107), *[FLAT] * 3]
    stopped, net, day, _ = _run([path], 3.0)
    assert stopped[0] and day[0] == 2
    assert net[0] == pytest.approx(107 / 100 - 1)


def test_same_day_new_high_does_not_raise_that_days_stop():
    path = [
        (100, 100.5, 99, 100),   # HWM 100.5, stop for day 2 = 97.5
        (100, 110, 98, 100),     # new high 110 but low 98 > 97.5 -> survives
        (100, 100, 100, 100),    # stop now 110 - 3 = 107: open 100 gaps through
        *[FLAT] * 2,
    ]
    stopped, net, day, gap = _run([path], 3.0)
    assert stopped[0] and day[0] == 3 and gap[0]
    assert net[0] == pytest.approx(0.0)  # filled at the day-3 open (100)


def test_time_stop_at_close_of_last_session():
    path = [(100, 101, 99, 100), (100, 102, 99, 101), (101, 103, 99.5, 102),
            (102, 103, 100.5, 102), (102, 103, 100.5, 102.5)]
    stopped, net, day, gap = _run([path], 3.0)
    assert not stopped[0] and day[0] == 5 and not gap[0]
    assert net[0] == pytest.approx(102.5 / 100 - 1)


def test_cost_is_subtracted_from_every_trade():
    from dataclasses import replace

    p = replace(P, cost=0.0005)
    stop_path = [(100, 101, 96.5, 99), *[FLAT] * 4]
    time_path = [FLAT] * 5
    _, net, _, _ = _run([stop_path, time_path], [3.0, 3.0], p)
    assert net[0] == pytest.approx(-0.03 - 0.0005)
    assert net[1] == pytest.approx(0.0 - 0.0005)


def test_distance_is_per_trade_and_trades_are_independent():
    path = [(100, 101, 98.5, 100), *[FLAT] * 4]
    stopped, net, day, _ = _run([path, path], [1.0, 3.0])
    assert list(stopped) == [True, False]  # stop 99 vs 97
    assert net[0] == pytest.approx(99 / 100 - 1)
    assert list(day) == [1, 5]


def test_stop_below_zero_is_never_hit():
    stopped, net, day, _ = _run([[(100, 101, 1, 100), *[FLAT] * 4]], 150.0)
    assert not stopped[0] and day[0] == 5 and net[0] == pytest.approx(0.0)


def test_single_session_window_is_valid():
    p = TrailingParams(max_days=1, cost=0.0)
    out = simulate_windows(
        np.array([[100.0]]), np.array([[101.0]]), np.array([[99.0]]),
        np.array([[100.5]]), np.array([3.0]), p,
    )
    assert not out[0][0] and out[2][0] == 1 and out[1][0] == pytest.approx(0.005)


@pytest.mark.parametrize("bad", [0.0, -1.0, np.nan, np.inf])
def test_invalid_distance_raises(bad):
    with pytest.raises(ValueError):
        _run([[FLAT] * 5], bad)


def test_distance_shape_mismatch_raises():
    with pytest.raises(ValueError):
        _run([[FLAT] * 5], [3.0, 3.0])


def test_summarize_is_date_weighted():
    trades = pd.DataFrame({
        "snapshot_date": ["d1", "d1", "d2"],
        "net_return": [0.10, -0.02, 0.04],
        "days_held": [60, 5, 20],
        "stopped": [False, True, True],
        "gap_exit": [False, True, False],
    })
    s = summarize(trades, P)
    assert s["n_trades"] == 3 and s["n_dates"] == 2
    assert s["avg_net_return"] == pytest.approx((0.04 + 0.04) / 2)  # (0.08/2, 0.04)
    assert s["win_rate"] == pytest.approx((0.5 + 1.0) / 2)
    assert s["stopped_share"] == pytest.approx((0.5 + 1.0) / 2)
    assert s["time_stop_share"] == pytest.approx(1 - s["stopped_share"])
    assert s["avg_days_held"] == pytest.approx((32.5 + 20) / 2)
    assert s["gap_exit_share"] == pytest.approx((0.5 + 0.0) / 2)
    assert s["worst_trade"] == pytest.approx(-0.02)
    assert s["best_trade"] == pytest.approx(0.10)
    assert summarize(trades.iloc[0:0], P) == {"n_trades": 0, "n_dates": 0}
