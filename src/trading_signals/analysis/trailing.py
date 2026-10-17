"""Trailing-stop trade simulation (medium-term trend trades).

Models the medium-term trade of the project (decision 2026-10-17, see
``docs/DECISIONS_FEATURES.md``): hold a ticker with a positive expected trend
and exit with a trailing stop; the exit is later meant to be Alpaca's native
``trailing_stop`` order with a fixed ``trail_price``.

Trade definition (pure numpy, same conventions as :mod:`analysis.barrier`):

* entry  = OPEN of the 1st session after the signal date d (= session 1, no
  lookahead); the entry price is ``E``
* trail distance ``D = atr_mult * ATR(d)`` in price units, FIXED for the whole
  trade (= Alpaca ``trail_price``); ATR comes from data up to and including d
* high-water mark ``HWM`` starts at ``E`` and only ever rises
* for session ``k = 1 .. max_days`` the stop is ``stop_k = HWM_{k-1} - D``
  (``HWM_0 = E``):

  - ``k >= 2`` and ``open_k <= stop_k`` (gap through the stop) -> exit at the
    OPEN of session k (realistic gap fill)
  - else ``low_k <= stop_k`` -> exit at ``stop_k``
  - else ``HWM_k = max(HWM_{k-1}, high_k)``
* not stopped after session ``max_days`` -> time stop at its CLOSE
* ``net = exit / E - 1 - cost`` (``cost`` = round trip, fraction)

Daily-bar approximation: the stop of session k only uses the high-water mark
up to session k-1; the high of session k raises the stop from session k+1 on.
Alpaca's real trailing stop follows the price intraday, so a day that makes a
new high and then falls ``D`` below it would be stopped out in reality but
not here. The label is therefore slightly optimistic on such round-trip days
(only daily OHLC bars are available). Intraday stop fills have no slippage.

If ``D >= HWM`` the stop price is not positive and can never be touched; the
trade then simply runs into the time stop (never happens for the S&P 500 /
Nasdaq-100 universe, where ATR(14) is a few percent of the price).

All functions are pure (numpy / pandas in, numpy / pandas out).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TrailingParams:
    atr_mult: float = 3.0  # trail distance = atr_mult * ATR
    atr_period: int = 14  # period of the ATR the distance is based on
    max_days: int = 60  # time stop: close of the max_days-th session
    cost: float = 0.0005  # round trip, fraction

    @property
    def label(self) -> str:
        return (
            f"trail {self.atr_mult:g} x ATR({self.atr_period}) / {self.max_days}d"
        )


#: Main trade definition, persisted as label in feature_snapshots
#: (``return_trail_60d``, ``trail_exit_day``, ``trail_stopped``).
MAIN_TRAILING = TrailingParams(atr_mult=3.0, atr_period=14, max_days=60, cost=0.0005)


def trail_distance(atr: float | np.ndarray, p: TrailingParams = MAIN_TRAILING):
    """Fixed trail distance in price units (``atr_mult * ATR``)."""
    return p.atr_mult * np.asarray(atr, dtype=float)


def simulate_windows(
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    dist: np.ndarray,
    p: TrailingParams = MAIN_TRAILING,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Simulate trailing-stop trades on price windows.

    Args:
        o, h, lo, c: arrays of shape ``(n, max_days)``; column 0 is the
            entry session (d+1). Rows must be complete (no NaN).
        dist: trail distance per trade in price units, shape ``(n,)``,
            finite and strictly positive.
        p: trade parameters (only ``cost`` is used; the window length is the
            array width).

    Returns:
        ``(stopped, net_return, exit_day, gap_exit)`` each of shape ``(n,)``.
        ``stopped`` is True for a trailing-stop exit and False for the time
        stop; ``exit_day`` is 1-based (time stop = ``max_days``);
        ``gap_exit`` marks a stop that filled at the open after a gap
        through the stop (never on day 1).
    """
    n, m = o.shape
    dist = np.asarray(dist, dtype=float)
    if dist.shape != (n,):
        raise ValueError(f"dist must have shape ({n},), got {dist.shape}")
    if not (np.isfinite(dist) & (dist > 0)).all():
        raise ValueError("trail distances must be finite and > 0")

    entry = o[:, 0]
    # High-water mark BEFORE each session: the entry price, then the running
    # maximum of the highs of the previous sessions.
    hwm_before = np.empty_like(h, dtype=float)
    hwm_before[:, 0] = entry
    if m > 1:
        running_high = np.maximum.accumulate(h, axis=1)
        hwm_before[:, 1:] = np.maximum(entry[:, None], running_high[:, :-1])
    stop = hwm_before - dist[:, None]

    gap = o <= stop
    gap[:, 0] = False  # the open of session 1 IS the entry price
    event = gap | (lo <= stop)

    has_event = event.any(axis=1)
    first = np.where(has_event, event.argmax(axis=1), m - 1)
    rows = np.arange(n)

    is_gap = has_event & gap[rows, first]
    exit_px = np.where(
        ~has_event, c[:, m - 1], np.where(is_gap, o[rows, first], stop[rows, first])
    )
    net = exit_px / entry - 1.0 - p.cost
    return has_event, net, first + 1, is_gap


def summarize(trades: pd.DataFrame, p: TrailingParams = MAIN_TRAILING) -> dict:
    """Date-weighted summary: every signal date counts equally.

    Args:
        trades: columns ``snapshot_date``, ``net_return``, ``days_held``
            (exit day), ``stopped`` and optionally ``gap_exit``.
    """
    if trades.empty:
        return {"n_trades": 0, "n_dates": 0}
    gap = trades["gap_exit"] if "gap_exit" in trades else False
    t = trades.assign(
        win=(trades["net_return"] > 0).astype(float),
        stopped=trades["stopped"].astype(float),
        gap=pd.Series(gap, index=trades.index).astype(float),
    )
    per_date = t.groupby("snapshot_date").agg(
        net=("net_return", "mean"), win=("win", "mean"), stopped=("stopped", "mean"),
        days=("days_held", "mean"), gap=("gap", "mean"),
    )
    return {
        "n_trades": int(len(t)),
        "n_dates": int(len(per_date)),
        "avg_net_return": float(per_date["net"].mean()),
        "win_rate": float(per_date["win"].mean()),
        "stopped_share": float(per_date["stopped"].mean()),
        "time_stop_share": float(1.0 - per_date["stopped"].mean()),
        "avg_days_held": float(per_date["days"].mean()),
        "gap_exit_share": float(per_date["gap"].mean()),
        "worst_trade": float(t["net_return"].min()),
        "best_trade": float(t["net_return"].max()),
    }
