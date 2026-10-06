"""Event studies on the barrier trade (concept §7.4, step D).

Question: do trades opened right after a dated event (8-K buyback
authorization, index addition, …) beat the universe on the same day?

Pure building blocks (unit-tested); DB loading and the report live in
``scripts/analysis/event_study.py``.

* :func:`label_meta` – barrier label rows (``y``, ``net``, ``net_cons``).
* :func:`dedupe_events` – one event per ticker within ``min_gap_days``.
* :func:`align_events` – signal date = first session ≥ event date, plus an
  optional delay in sessions (the trade then opens at ``open(d+1)``).
* :func:`event_selection` – event rows joined to the label of (ticker, d).
* :func:`evaluate_events` – date-weighted comparison with the universe on
  the same dates (same statistics as step B) plus trade-weighted means.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from trading_signals.analysis.hit_model import (
    BREAK_EVEN_HIT_RATE,
    DATE_COL,
    STOP_NET,
    evaluate_selection,
)

__all__ = [
    "BREAK_EVEN_HIT_RATE",
    "align_events",
    "dedupe_events",
    "evaluate_events",
    "event_selection",
    "label_meta",
    "passes_criterion",
]


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
