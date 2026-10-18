"""Persist the screen candidates of a context pack (``signals.pack_candidates``).

The context pack generator calls the screen (``analysis/screen.py``) for the
pack session and writes the tickers that got a bucket (``bull`` / ``bear`` /
``random`` / ``requested``) here, so the forecast evaluation can compare
Claude's calls with the shortlist of the same day (``claude_value_add`` mode,
FORECASTS.md section 6).

* :func:`pack_candidate_rows` - pure: the rows of ONE session from the result
  of :func:`~trading_signals.analysis.screen.screen_cross_section`.
* :func:`replace_pack_candidates` - idempotent write: a re-run of the pack of a
  session replaces that session's rows.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import pandas as pd

from trading_signals.analysis.screen import (
    BUCKET_BEAR,
    BUCKET_BULL,
    BUCKET_RANDOM,
    BUCKET_REQUESTED,
    GROUP_NAMES,
    SCREEN_VERSION,
)

BUCKETS = (BUCKET_BULL, BUCKET_BEAR, BUCKET_RANDOM, BUCKET_REQUESTED)


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _split(text: Any) -> list[str]:
    return [g for g in str(text or "").split(",") if g]


def pack_candidate_rows(
    screened: pd.DataFrame, session_date: date, screen_version: str = SCREEN_VERSION
) -> list[dict[str, Any]]:
    """Rows of ``pack_candidates`` for the bucketed tickers of one screen run.

    Args:
        screened: Result of ``screen_cross_section`` of ONE session (columns
            ``ticker``, ``bucket``, ``bucket_rank``, ``gate_ok``,
            ``evidence_breadth``, ``composite``, ``gs_<group>``, ``gp_<group>``,
            ``ev_<event>``, ``bull_groups`` ...). Rows without a bucket are
            skipped.

    Returns:
        One dict per candidate (the keyword arguments of ``PackCandidate``):
        ``group_scores`` = ``{group: score}`` of the groups with a score,
        ``flags`` = event flags, group percentiles, bullish / bearish groups,
        ``is_requested`` and the ``gate_reasons``.
    """
    rows: list[dict[str, Any]] = []
    sel = screened[screened["bucket"].isin(BUCKETS)]
    events = [c for c in screened.columns if c.startswith("ev_")]
    for _, r in sel.iterrows():
        scores = {g: _num(r.get(f"gs_{g}")) for g in GROUP_NAMES}
        pcts = {g: _num(r.get(f"gp_{g}")) for g in GROUP_NAMES}
        breadth = _num(r.get("evidence_breadth"))
        rank = _num(r.get("bucket_rank"))
        flags: dict[str, Any] = {c: bool(r[c]) for c in events}
        flags.update(
            {
                "group_pct": {g: v for g, v in pcts.items() if v is not None},
                "bull_groups": _split(r.get("bull_groups")),
                "bear_groups": _split(r.get("bear_groups")),
                "is_requested": bool(r.get("is_requested", False)),
                "gate_reasons": str(r.get("gate_reasons") or ""),
            }
        )
        rows.append(
            {
                "session_date": session_date,
                "ticker": str(r["ticker"]),
                "bucket": str(r["bucket"]),
                "bucket_rank": int(rank) if rank else None,
                "screen_version": screen_version,
                "gate_ok": bool(r["gate_ok"]),
                "composite": _num(r.get("composite")),
                "evidence_breadth": int(breadth) if breadth is not None else None,
                "group_scores": {g: v for g, v in scores.items() if v is not None},
                "flags": flags,
            }
        )
    return rows


def replace_pack_candidates(
    session, session_date: date, rows: Sequence[Mapping[str, Any]]
) -> int:
    """Replace the stored candidates of ``session_date`` by ``rows``.

    The caller commits.
    """
    from sqlalchemy import delete

    from trading_signals.db.models.forecasts import PackCandidate

    session.execute(
        delete(PackCandidate).where(PackCandidate.session_date == session_date)
    )
    for row in rows:
        session.add(PackCandidate(**row))
    session.flush()
    return len(rows)


__all__ = ["pack_candidate_rows", "replace_pack_candidates"]
