"""Tests for the barrier trade label (migration 033)."""

from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from trading_signals.analysis.barrier import BarrierParams
from trading_signals.analysis.feature_report import (
    FALLBACK_TARGET,
    MODEL_TARGET,
    resolve_model_target,
)
from trading_signals.derived.target_backfill import (
    TargetBackfillComputer,
    compute_barrier_labels,
)

SESSIONS = [date(2026, 6, 29), date(2026, 6, 30), date(2026, 7, 1),
            date(2026, 7, 2), date(2026, 7, 6), date(2026, 7, 7)]
P3 = BarrierParams(tp=0.01, sl=0.02, max_days=3, cost=0.0, ambiguous="ohlc")
COLS = ["ticker", "trade_date", "open", "high", "low", "close", "is_extrapolated"]


def _px(rows, ticker="AAA"):
    """rows: (session index, o, h, l, c[, extrapolated])."""
    out = []
    for r in rows:
        i, o, h, lo, c = r[:5]
        ext = r[5] if len(r) > 5 else False
        out.append((ticker, SESSIONS[i], o, h, lo, c, ext))
    return pd.DataFrame(out, columns=COLS)


def _flat(n=6, ticker="AAA"):
    return _px([(i, 100, 100.5, 99.5, 100) for i in range(n)], ticker)


def _snaps(dates, ticker="AAA"):
    return pd.DataFrame({"ticker": [ticker] * len(dates), "snapshot_date": dates})


def test_take_profit_on_second_window_day():
    px = _flat()
    px.loc[2, ["high", "close"]] = [101.5, 101]  # session 2 = window day 2 for d=0
    out = compute_barrier_labels(_snaps([SESSIONS[0]]), px, SESSIONS, P3)
    assert out.loc[0, "barrier_outcome"] == 1
    assert out.loc[0, "return_barrier_14d"] == pytest.approx(0.01)
    assert out.loc[0, "barrier_ambiguous"] is False


def test_time_stop():
    out = compute_barrier_labels(_snaps([SESSIONS[0]]), _flat(), SESSIONS, P3)
    assert out.loc[0, "barrier_outcome"] == 0
    assert out.loc[0, "return_barrier_14d"] == pytest.approx(0.0)


def test_window_beyond_data_is_null():
    out = compute_barrier_labels(_snaps([SESSIONS[3]]), _flat(), SESSIONS, P3)
    assert np.isnan(out.loc[0, "return_barrier_14d"])
    assert out.loc[0, "barrier_outcome"] is None


def test_missing_or_extrapolated_row_is_null():
    px = _flat().drop(index=2)
    out = compute_barrier_labels(_snaps([SESSIONS[0]]), px, SESSIONS, P3)
    assert out.loc[0, "barrier_outcome"] is None
    px = _flat()
    px.loc[2, "is_extrapolated"] = True
    out = compute_barrier_labels(_snaps([SESSIONS[0]]), px, SESSIONS, P3)
    assert out.loc[0, "barrier_outcome"] is None


def test_non_session_snapshot_is_null():
    out = compute_barrier_labels(_snaps([date(2026, 7, 4)]), _flat(), SESSIONS, P3)
    assert out.loc[0, "barrier_outcome"] is None


def test_tickers_independent():
    a = _flat(ticker="AAA")
    b = _flat(ticker="BBB")
    b.loc[1, "low"] = 97.0  # stop on entry day for BBB
    snaps = pd.concat([_snaps([SESSIONS[0]], "AAA"), _snaps([SESSIONS[0]], "BBB")])
    out = compute_barrier_labels(snaps, pd.concat([a, b]), SESSIONS, P3)
    assert list(out["barrier_outcome"]) == [0, -1]


def test_bulk_update_writes_barrier_columns():
    session = MagicMock()
    result = pd.DataFrame({
        "ticker": ["AAA", "BBB"], "snapshot_date": [SESSIONS[0]] * 2,
        "return_1d": [0.01, float("nan")], "return_5d": [float("nan")] * 2,
        "return_20d": [float("nan")] * 2, "return_60d": [float("nan")] * 2,
        "return_barrier_14d": [0.0095, float("nan")],
        "barrier_outcome": [1, None], "barrier_ambiguous": [False, None],
    })
    filled = TargetBackfillComputer(session)._bulk_update(result)
    assert filled == 2
    params = session.execute.call_args[0][1]
    assert params[0]["return_barrier_14d"] == pytest.approx(0.0095)
    assert params[0]["barrier_outcome"] == 1 and params[0]["barrier_ambiguous"] is False
    assert params[1]["return_barrier_14d"] is None
    assert params[1]["barrier_outcome"] is None and params[1]["barrier_ambiguous"] is None


def test_resolve_model_target():
    df = pd.DataFrame({FALLBACK_TARGET: [0.1, 0.2, 0.3, 0.4]})
    assert resolve_model_target(df)[0] == FALLBACK_TARGET
    df[MODEL_TARGET] = [np.nan, np.nan, np.nan, 0.1]  # 25 % coverage
    assert resolve_model_target(df)[0] == FALLBACK_TARGET
    df[MODEL_TARGET] = [0.1, 0.2, np.nan, 0.1]  # 75 % coverage
    assert resolve_model_target(df) == (MODEL_TARGET, 14)
