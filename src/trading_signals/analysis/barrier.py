"""Barrier trade simulation (take profit / stop loss / time stop).

Models the actual short-term trade of the project (see
docs/2026-10-06_Konzept_Kurzfrist_Kandidaten.md):

* entry  = OPEN of the 1st session after the signal date d (no lookahead)
* exit   = first of
    - take profit  ``entry * (1 + tp)``
    - stop loss    ``entry * (1 - sl)``
    - time stop    CLOSE of the ``max_days``-th session (entry day = day 1)
* a gap through a barrier at the open fills at the OPEN (realistic slippage /
  price improvement); if TP and SL are both touched within the same day the
  STOP counts (conservative – only daily bars are available)
* ``cost`` (round trip, as a fraction) is subtracted from every trade

All functions are pure (numpy / pandas in, numpy / pandas out).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

OUTCOME_TP = 1
OUTCOME_SL = -1
OUTCOME_TIME = 0


@dataclass(frozen=True)
class BarrierParams:
    tp: float  # take profit, fraction (0.01 = +1 %)
    sl: float  # stop loss, fraction (0.02 = -2 %)
    max_days: int = 14
    cost: float = 0.0005  # round trip

    @property
    def label(self) -> str:
        return f"TP {self.tp * 100:.2f}% / SL {self.sl * 100:.2f}% / {self.max_days}d"

    @property
    def random_hit_rate(self) -> float:
        """Hit rate of a driftless random walk = break-even hit rate (gross)."""
        return self.sl / (self.tp + self.sl)


def simulate_windows(
    o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, p: BarrierParams
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Simulate trades on price windows.

    Args:
        o, h, lo, c: arrays of shape ``(n, max_days)``; column 0 is the
            entry session (d+1). Rows must be complete (no NaN).
        p: barrier parameters.

    Returns:
        ``(outcome, net_return, days_held)`` each of shape ``(n,)``.
        ``outcome`` ∈ {1 TP, -1 SL, 0 time stop}; ``days_held`` is 1-based.
    """
    n, m = o.shape
    entry = o[:, 0]
    tp_px = (entry * (1.0 + p.tp))[:, None]
    sl_px = (entry * (1.0 - p.sl))[:, None]

    gap_sl = o <= sl_px  # never true on day 0 (o == entry)
    gap_tp = o >= tp_px  # never true on day 0 (tp > 0)
    hit_sl = lo <= sl_px
    hit_tp = h >= tp_px
    event = gap_sl | gap_tp | hit_sl | hit_tp

    has_event = event.any(axis=1)
    first = np.where(has_event, event.argmax(axis=1), m - 1)
    rows = np.arange(n)

    g_sl = gap_sl[rows, first]
    g_tp = gap_tp[rows, first]
    i_sl = hit_sl[rows, first]
    open_j = o[rows, first]

    exit_px = np.where(
        ~has_event, c[:, m - 1],
        np.where(g_sl, open_j,
                 np.where(g_tp, open_j,
                          np.where(i_sl, sl_px[:, 0], tp_px[:, 0]))),
    )
    outcome = np.where(
        ~has_event, OUTCOME_TIME,
        np.where(g_sl | (~g_tp & i_sl), OUTCOME_SL, OUTCOME_TP),
    )
    net = exit_px / entry - 1.0 - p.cost
    return outcome.astype(np.int8), net, first + 1


def simulate_signals(
    signals: pd.DataFrame, prices: pd.DataFrame, p: BarrierParams
) -> pd.DataFrame:
    """Barrier outcome for every (snapshot_date, ticker) signal.

    Args:
        signals: columns ``snapshot_date``, ``ticker`` (other columns kept).
        prices: columns ``ticker``, ``trade_date``, ``open``, ``high``,
            ``low``, ``close``; extrapolated rows must already be removed.
        p: barrier parameters.

    Returns:
        ``signals`` (only rows with a complete forward window) plus
        ``outcome``, ``net_return``, ``days_held``, ``entry_date``.
    """
    m = p.max_days
    out = []
    px = prices.sort_values(["ticker", "trade_date"])
    by_ticker = {t: g for t, g in px.groupby("ticker", sort=False)}
    for ticker, sig in signals.groupby("ticker", sort=False):
        g = by_ticker.get(ticker)
        if g is None or len(g) < m:
            continue
        dates = pd.to_datetime(g["trade_date"]).to_numpy()
        arr = g[["open", "high", "low", "close"]].to_numpy(dtype=float)
        # entry row = first trading row strictly after the signal date
        sig_dates = pd.to_datetime(sig["snapshot_date"]).to_numpy()
        start = np.searchsorted(dates, sig_dates, side="right")
        ok = start + m <= len(dates)
        # entry must be the next session (≤ 5 calendar days incl. weekend/holiday)
        entry_dates = dates[np.minimum(start, len(dates) - 1)]
        ok &= (entry_dates - sig_dates) <= np.timedelta64(5, "D")
        if not ok.any():
            continue
        sig = sig[ok]
        start = start[ok]
        idx = start[:, None] + np.arange(m)[None, :]
        win = arr[idx]  # (n, m, 4)
        complete = ~np.isnan(win).any(axis=(1, 2)) & (win[:, 0, 0] > 0)
        if not complete.any():
            continue
        sig = sig[complete]
        win = win[complete]
        outcome, net, days = simulate_windows(
            win[:, :, 0], win[:, :, 1], win[:, :, 2], win[:, :, 3], p
        )
        res = sig.copy()
        res["outcome"] = outcome
        res["net_return"] = net
        res["days_held"] = days
        res["entry_date"] = dates[start[complete]]
        out.append(res)
    if not out:
        cols = [*signals.columns, "outcome", "net_return", "days_held", "entry_date"]
        return pd.DataFrame(columns=cols)
    return pd.concat(out, ignore_index=True)


def summarize(trades: pd.DataFrame, p: BarrierParams) -> dict:
    """Date-weighted summary: every signal date counts equally."""
    if trades.empty:
        return {"n_trades": 0, "n_dates": 0}
    t = trades.assign(hit=(trades["outcome"] == OUTCOME_TP).astype(float),
                      time=(trades["outcome"] == OUTCOME_TIME).astype(float))
    per_date = t.groupby("snapshot_date").agg(
        hit=("hit", "mean"), net=("net_return", "mean"),
        time=("time", "mean"), days=("days_held", "mean"),
    )
    return {
        "n_trades": int(len(t)),
        "n_dates": int(len(per_date)),
        "hit_rate": float(per_date["hit"].mean()),
        "random_hit_rate": p.random_hit_rate,
        "avg_net_return": float(per_date["net"].mean()),
        "time_stop_share": float(per_date["time"].mean()),
        "avg_days_held": float(per_date["days"].mean()),
        "worst_trade": float(t["net_return"].min()),
    }


def block_bootstrap_diff(
    a: pd.Series, b: pd.Series, block: int = 20, n_boot: int = 2000, seed: int = 0
) -> tuple[float, float, float]:
    """Mean difference ``a - b`` of two per-date series with a date-block bootstrap.

    Returns ``(mean_diff, ci_low_95, ci_high_95)``. Only dates present in
    both series are used; consecutive blocks of ``block`` dates are resampled
    to respect overlapping holding periods.
    """
    d = (a - b).dropna().sort_index().to_numpy()
    if len(d) == 0:
        return float("nan"), float("nan"), float("nan")
    block = max(1, min(block, len(d)))
    n_blocks = int(np.ceil(len(d) / block))
    starts_max = len(d) - block + 1
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, starts_max, size=n_blocks)
        sample = np.concatenate([d[s:s + block] for s in starts])[: len(d)]
        means[i] = sample.mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(d.mean()), float(lo), float(hi)
