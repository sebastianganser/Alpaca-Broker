"""Tests for trading_signals.analysis.barrier."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_signals.analysis.barrier import (
    OUTCOME_SL,
    OUTCOME_TIME,
    OUTCOME_TP,
    BarrierParams,
    block_bootstrap_diff,
    simulate_signals,
    simulate_windows,
    summarize,
)

P = BarrierParams(tp=0.01, sl=0.02, max_days=3, cost=0.0)


def _w(rows):
    """rows: list of trades, each a list of (o, h, l, c) per day."""
    a = np.array(rows, dtype=float)
    return a[:, :, 0], a[:, :, 1], a[:, :, 2], a[:, :, 3]


def test_take_profit_intraday():
    o, h, lo, c = _w([[(100, 100.5, 99.5, 100), (100, 101.2, 99.8, 101)]
                      + [(101, 101, 101, 101)]])
    out, net, days, _ = simulate_windows(o, h, lo, c, P)
    assert out[0] == OUTCOME_TP and days[0] == 2
    assert net[0] == pytest.approx(0.01)


def test_stop_wins_when_both_touched_same_day():
    o, h, lo, c = _w([[(100, 101.5, 97.5, 100), (100, 100, 100, 100), (100, 100, 100, 100)]])
    out, net, days, _ = simulate_windows(o, h, lo, c, P)
    assert out[0] == OUTCOME_SL and days[0] == 1
    assert net[0] == pytest.approx(-0.02)


def test_ohlc_rule_resolves_ambiguous_day():
    from dataclasses import replace

    p = replace(P, ambiguous="ohlc")
    # red candle (close < open): O→H→L→C → take profit first
    o, h, lo, c = _w([[(100, 101.5, 97.5, 99), (100, 100, 100, 100), (100, 100, 100, 100)]])
    out, net, _, amb = simulate_windows(o, h, lo, c, p)
    assert amb[0] and out[0] == OUTCOME_TP and net[0] == pytest.approx(0.01)
    # green candle (close ≥ open): O→L→H→C → stop first
    o, h, lo, c = _w([[(100, 101.5, 97.5, 101), (100, 100, 100, 100), (100, 100, 100, 100)]])
    out, _, _, amb = simulate_windows(o, h, lo, c, p)
    assert amb[0] and out[0] == OUTCOME_SL


def test_unambiguous_trade_not_flagged():
    o, h, lo, c = _w([[(100, 101.2, 99.5, 101), (100, 100, 100, 100), (100, 100, 100, 100)]])
    _, _, _, amb = simulate_windows(o, h, lo, c, P)
    assert not amb[0]


def test_gap_down_fills_at_open():
    o, h, lo, c = _w([[(100, 100.5, 99, 99.5), (95, 96, 94, 95), (95, 95, 95, 95)]])
    out, net, days, _ = simulate_windows(o, h, lo, c, P)
    assert out[0] == OUTCOME_SL and days[0] == 2
    assert net[0] == pytest.approx(-0.05)


def test_gap_up_fills_at_open():
    o, h, lo, c = _w([[(100, 100.5, 99.5, 100), (103, 104, 102, 103), (103, 103, 103, 103)]])
    out, net, _, _ = simulate_windows(o, h, lo, c, P)
    assert out[0] == OUTCOME_TP
    assert net[0] == pytest.approx(0.03)


def test_time_stop_and_cost():
    p = BarrierParams(tp=0.01, sl=0.02, max_days=3, cost=0.001)
    o, h, lo, c = _w([[(100, 100.5, 99.5, 100), (100, 100.5, 99.5, 100.2), (100, 100.5, 99.5, 100.4)]])
    out, net, days, _ = simulate_windows(o, h, lo, c, p)
    assert out[0] == OUTCOME_TIME and days[0] == 3
    assert net[0] == pytest.approx(0.004 - 0.001)


def test_random_hit_rate():
    assert BarrierParams(tp=0.005, sl=0.015).random_hit_rate == pytest.approx(0.75)


def _prices(ticker, start, closes):
    dates = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({
        "ticker": ticker, "trade_date": dates,
        "open": closes, "high": [x * 1.001 for x in closes],
        "low": [x * 0.999 for x in closes], "close": closes,
    })


def test_simulate_signals_entry_next_session_and_incomplete_dropped():
    px = _prices("AAA", "2026-01-05", [100, 100, 102, 102, 102, 102])
    sig = pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2026-01-05", "2026-01-09", "2026-01-12"]),
        "ticker": "AAA",
    })
    res = simulate_signals(sig, px, P)
    # 2026-01-05 → entry 01-06 (open 100), TP on 01-07 open gap (102)
    assert len(res) == 1
    row = res.iloc[0]
    assert row["entry_date"] == pd.Timestamp("2026-01-06")
    assert row["outcome"] == OUTCOME_TP
    assert row["net_return"] == pytest.approx(0.02)


def test_simulate_signals_requires_adjacent_entry():
    px = _prices("AAA", "2026-01-05", [100] * 10)
    sig = pd.DataFrame({"snapshot_date": pd.to_datetime(["2025-12-01"]), "ticker": "AAA"})
    assert simulate_signals(sig, px, P).empty


def test_summarize_date_weighted():
    tr = pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2026-01-05"] * 2 + ["2026-01-06"]),
        "outcome": [OUTCOME_TP, OUTCOME_SL, OUTCOME_TP],
        "net_return": [0.01, -0.02, 0.01],
        "days_held": [1, 2, 3],
    })
    s = summarize(tr, P)
    assert s["hit_rate"] == pytest.approx(0.75)
    assert s["avg_net_return"] == pytest.approx((-0.005 + 0.01) / 2)


def test_block_bootstrap_detects_shift():
    idx = pd.date_range("2026-01-01", periods=200)
    rng = np.random.default_rng(1)
    a = pd.Series(rng.normal(0.01, 0.001, 200), index=idx)
    b = pd.Series(rng.normal(0.0, 0.001, 200), index=idx)
    diff, lo, hi = block_bootstrap_diff(a, b, block=10, n_boot=300)
    assert lo > 0 and diff == pytest.approx(0.01, abs=0.001)
