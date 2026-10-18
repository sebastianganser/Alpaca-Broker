"""Event studies: do tickers earn more after a dated event than the universe?

Pure building blocks (unit-tested); DB loading and the report live in
``scripts/analysis/event_study.py``. Two evaluation modes:

**Horizon mode (strategy-neutral, plan 2026-10-17 step 2)** - the forward
returns ``return_20d`` / ``return_60d`` (``close(d+h) / open(d+1) - 1``) after
the event, raw and as excess over the same-day universe mean and over SPY,
date-weighted with a 95 % batch-jackknife interval (batches of 2 x horizon event
dates, t critical value; a date-block bootstrap is too narrow at this sample
size, see ``analysis.criterion_sim``). The "dates" are the dates that carry an
event - they are sparse, so a batch spans more calendar time than 2h sessions,
which only makes the interval more cautious:

* :func:`horizon_meta` - labelled rows with ``ret_<h>``, ``ex_<h>`` (and SPY).
* :func:`evaluate_events_horizon` - events vs the universe on the same dates.
* :func:`passes_criterion_horizon` - decision rule (same shape as D1).

**Barrier mode (older trade concept, deprecated)** - kept until the barrier
label is deleted (plan step 6, together with ``hit_model``);
everything below the marked section can be removed with it:
:func:`label_meta`, :func:`evaluate_events`, :func:`passes_criterion`.

Shared by both modes:

* :func:`dedupe_events` - one event per ticker within ``min_gap_days``.
* :func:`align_events` - signal date = first session >= event date, plus an
  optional delay in sessions (the position then starts at ``open(d+1)``).
* :func:`event_selection` - event rows joined to the label rows of
  (ticker, d).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from trading_signals.analysis.stats import (
    DATE_COL,
    batch_jackknife_diff,
    has_batch_ci,
    to_float,
)

# Barrier mode only - delete with the barrier label in step 6:
from trading_signals.analysis.hit_model import (  # isort: skip
    BREAK_EVEN_HIT_RATE,
    STOP_NET,
    evaluate_selection,
)

__all__ = [
    "BREAK_EVEN_HIT_RATE",
    "HORIZONS",
    "MIN_EVENTS",
    "align_events",
    "dedupe_events",
    "evaluate_events",
    "evaluate_events_horizon",
    "event_selection",
    "horizon_meta",
    "label_meta",
    "passes_criterion",
    "passes_criterion_horizon",
]

#: Horizons of the strategy-neutral mode (sessions).
HORIZONS = (20, 60)
#: Minimum number of events for a verdict (as in D1).
MIN_EVENTS = 150


# ── Shared building blocks ───────────────────────────────────────────


def dedupe_events(events: pd.DataFrame, min_gap_days: int = 5) -> pd.DataFrame:
    """Keep the first event per ticker; drop repeats within ``min_gap_days``.

    Several 8-Ks often describe the same authorization (press release,
    earnings release, later amendment) – they must not count twice.
    """
    if events.empty:
        return events.copy()
    ev = events.assign(event_date=pd.to_datetime(events["event_date"]))
    ev = ev.sort_values(["ticker", "event_date"], kind="stable")
    keep = []
    last: dict[str, pd.Timestamp] = {}
    for idx, row in ev.iterrows():
        prev = last.get(row["ticker"])
        if prev is None or (row["event_date"] - prev).days > min_gap_days:
            keep.append(idx)
            last[row["ticker"]] = row["event_date"]
    return ev.loc[keep].reset_index(drop=True)


def align_events(
    events: pd.DataFrame, sessions: Sequence, delay: int = 0
) -> pd.DataFrame:
    """Add ``snapshot_date`` = first session ≥ ``event_date``, shifted by ``delay``.

    Events after the last session (or shifted past it) are dropped.
    """
    sess = pd.DatetimeIndex(pd.to_datetime(pd.Series(sessions))).sort_values().unique()
    ev = events.assign(event_date=pd.to_datetime(events["event_date"]))
    pos = sess.searchsorted(ev["event_date"].to_numpy(), side="left") + delay
    ok = pos < len(sess)
    out = ev.loc[ok].copy()
    out[DATE_COL] = sess[pos[ok]]
    return out.reset_index(drop=True)


def event_selection(aligned: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    """Join aligned events to their trade label; one row per (ticker, date)."""
    extra = [c for c in aligned.columns if c not in (DATE_COL, "ticker")]
    sel = aligned[[DATE_COL, "ticker", *extra]].merge(
        meta, on=[DATE_COL, "ticker"], how="inner"
    )
    return sel.drop_duplicates([DATE_COL, "ticker"]).reset_index(drop=True)


# ── Horizon mode (strategy-neutral) ──────────────────────────────────


def horizon_meta(
    df: pd.DataFrame,
    horizons: Sequence[int] = HORIZONS,
    spy: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Snapshot rows with realised returns -> ``ret_<h>`` / ``ex_<h>`` per horizon.

    Args:
        df: ``snapshot_date``, ``ticker`` and ``return_<h>d`` per horizon (NaN
            while the window is still open).
        spy: Optional SPY ``return_<h>d`` indexed by session (or with a
            ``snapshot_date`` column); adds ``spy_<h>`` and ``exspy_<h>``.

    ``ex_<h>`` is the return minus the mean return of all rows of the same
    session in ``df`` (the universe passed in). Not yet realised -> NaN.
    """
    out = pd.DataFrame({
        DATE_COL: pd.to_datetime(df[DATE_COL]).dt.normalize().to_numpy(),
        "ticker": df["ticker"].astype(str).to_numpy(),
    })
    spy_idx = None
    if spy is not None:
        spy_idx = spy.copy()
        if not isinstance(spy_idx.index, pd.DatetimeIndex):
            spy_idx = spy_idx.set_index(DATE_COL)
        spy_idx.index = pd.to_datetime(spy_idx.index).normalize()
    for h in horizons:
        ret = f"ret_{h}"
        out[ret] = to_float(df[f"return_{h}d"]).to_numpy()
        out[f"ex_{h}"] = out[ret] - out.groupby(DATE_COL)[ret].transform("mean")
        if spy_idx is not None and f"return_{h}d" in spy_idx.columns:
            out[f"spy_{h}"] = out[DATE_COL].map(spy_idx[f"return_{h}d"])
            out[f"exspy_{h}"] = out[ret] - out[f"spy_{h}"]
    return out


def evaluate_events_horizon(
    selected: pd.DataFrame,
    meta: pd.DataFrame,
    horizon: int,
    bootstrap: bool = True,
) -> dict[str, Any]:
    """Event returns vs. the universe on the same dates for one horizon.

    Date-weighted (every event date counts once): ``mean_ret`` and
    ``uni_mean`` (raw), ``diff`` = excess over the universe (95 % batch
    jackknife ``diff_lo`` / ``diff_hi``; ``bootstrap=False`` skips it),
    ``hit_rate`` / ``uni_hit_rate`` (share of positive returns) with
    ``hit_edge`` (+ CI) and, if ``spy_<h>`` is present, ``spy_mean`` and
    ``diff_spy`` (+ CI) = excess over SPY. Event-weighted figures:
    ``event_mean``, ``event_ex_mean``, ``event_hit_rate``. Events whose window
    is still open (no realised return) are counted in ``n_unrealised``.
    """
    h = int(horizon)
    ret = f"ret_{h}"
    uni = meta[meta[ret].notna()]
    sel = selected[selected[ret].notna()]
    out: dict[str, Any] = {
        "horizon": h, "n_days": int(uni[DATE_COL].nunique()),
        "n_events": int(len(sel)), "n_unrealised": int(len(selected) - len(sel)),
        "days_with": 0,
    }
    if sel.empty:
        return out
    per_sel = sel.groupby(DATE_COL)[ret].mean()
    per_uni = uni.groupby(DATE_COL)[ret].mean().reindex(per_sel.index)
    hit_sel = (sel[ret] > 0).astype(float).groupby(sel[DATE_COL]).mean()
    hit_uni = ((uni[ret] > 0).astype(float).groupby(uni[DATE_COL]).mean()
               .reindex(per_sel.index))
    out.update(
        days_with=int(len(per_sel)),
        mean_ret=float(per_sel.mean()), uni_mean=float(per_uni.mean()),
        diff=float((per_sel - per_uni).mean()),
        hit_rate=float(hit_sel.mean()), uni_hit_rate=float(hit_uni.mean()),
        event_mean=float(sel[ret].mean()), event_ex_mean=float(sel[f"ex_{h}"].mean()),
        event_hit_rate=float((sel[ret] > 0).mean()),
    )
    spy_col = f"spy_{h}"
    per_spy = None
    if spy_col in sel.columns and sel[spy_col].notna().any():
        per_spy = sel.groupby(DATE_COL)[spy_col].mean()
        out["spy_mean"] = float(per_spy.reindex(per_sel.index).mean())
        out["diff_spy"] = float((per_sel - per_spy).dropna().mean())
    if bootstrap and has_batch_ci(len(per_sel), h):
        _, out["diff_lo"], out["diff_hi"] = batch_jackknife_diff(
            per_sel, per_uni, h)
        _, out["hit_edge_lo"], out["hit_edge_hi"] = batch_jackknife_diff(
            hit_sel, hit_uni, h)
        if per_spy is not None:
            _, out["diff_spy_lo"], out["diff_spy_hi"] = batch_jackknife_diff(
                per_sel, per_spy, h)
    out["hit_edge"] = out["hit_rate"] - out["uni_hit_rate"]
    return out


def passes_criterion_horizon(
    stats: dict[str, Any], min_events: int = MIN_EVENTS
) -> bool:
    """Decision rule of the horizon mode (same shape as the D1 criterion).

    At least ``min_events`` events, positive date-weighted mean return AND the
    95 % jackknife CI of (events - universe) above zero. Evaluated on the
    primary 20-session horizon; a re-test of an already inspected sample, so
    it is exploratory evidence, not a fresh pre-registered confirmation.
    """
    return bool(
        stats.get("n_events", 0) >= min_events
        and stats.get("mean_ret", float("nan")) > 0
        and stats.get("diff_lo", float("nan")) > 0
    )


# ── Barrier mode (DEPRECATED - removed with the barrier label, step 6) ──


def label_meta(df: pd.DataFrame) -> pd.DataFrame:
    """Labelled snapshot rows → ``snapshot_date, ticker, y, net, net_cons``.

    ``net_cons`` counts take-profit trades on ambiguous days (target and
    stop touched on the same day) as stopped – the conservative rule.
    """
    sub = df[df["barrier_outcome"].notna() & df["return_barrier_14d"].notna()]
    outcome = pd.to_numeric(sub["barrier_outcome"], errors="coerce")
    net = pd.to_numeric(sub["return_barrier_14d"], errors="coerce").astype(float)
    amb = sub["barrier_ambiguous"].fillna(False).astype(bool)
    return pd.DataFrame({
        DATE_COL: pd.to_datetime(sub[DATE_COL]),
        "ticker": sub["ticker"].astype(str),
        "y": (outcome == 1).astype(int),
        "net": net,
        "net_cons": np.where(amb & (outcome == 1), STOP_NET, net),
    }).reset_index(drop=True)


def evaluate_events(
    selected: pd.DataFrame, meta: pd.DataFrame, block: int = 20, bootstrap: bool = True
) -> dict[str, Any]:
    """Statistics of event trades vs. the universe on the same dates.

    Date-weighted figures (``net``, ``diff`` …) come from
    :func:`hit_model.evaluate_selection`; ``n_events``, ``trade_net`` and
    ``trade_hit_rate`` are trade-weighted.
    """
    out = evaluate_selection(selected, meta, block=block, bootstrap=bootstrap)
    out["n_events"] = int(len(selected))
    if not selected.empty:
        out["trade_net"] = float(selected["net"].mean())
        out["trade_net_cons"] = float(selected["net_cons"].mean())
        out["trade_hit_rate"] = float(selected["y"].mean())
    return out


def passes_criterion(stats: dict[str, Any], min_events: int = 150) -> bool:
    """Pre-registered success criterion (concept §7.4, D1)."""
    return bool(
        stats.get("n_events", 0) >= min_events
        and stats.get("net", float("nan")) > 0
        and stats.get("diff_lo", float("nan")) > 0
    )
