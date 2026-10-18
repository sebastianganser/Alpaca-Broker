"""Unit tests for derived/pack_candidates.py (no DB)."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

from tests.unit.screen_data import cross_section
from trading_signals.analysis.screen import (
    SCREEN_VERSION,
    ScreenConfig,
    screen_cross_section,
)
from trading_signals.db.models.forecasts import PackCandidate
from trading_signals.derived.pack_candidates import (
    pack_candidate_rows,
    replace_pack_candidates,
)

DAY = date(2024, 3, 1)


def _screen():
    df = cross_section("2024-03-01", n=150, seed=3)
    return screen_cross_section(
        df, config=ScreenConfig(bearish_enabled=True), requested=["T000"]
    )


def test_rows_cover_exactly_the_bucketed_tickers():
    screened = _screen()
    rows = pack_candidate_rows(screened, DAY)
    buckets = {
        b: [r for r in rows if r["bucket"] == b]
        for b in ("bull", "bear", "random", "requested")
    }
    assert 1 <= len(buckets["bull"]) <= 10 and len(buckets["random"]) == 5
    assert len(rows) == int((screened["bucket"] != "").sum())
    assert len({(r["ticker"], r["bucket"]) for r in rows}) == len(rows)  # PK is unique
    assert all(
        r["session_date"] == DAY and r["screen_version"] == SCREEN_VERSION for r in rows
    )


def test_a_row_carries_the_screen_result_and_is_json_safe():
    screened = _screen()
    rows = pack_candidate_rows(screened, DAY)
    bull = next(r for r in rows if r["bucket"] == "bull" and r["bucket_rank"] == 1)
    src = screened[screened["ticker"] == bull["ticker"]].iloc[0]
    assert bull["gate_ok"] is True and bull["evidence_breadth"] == int(
        src["evidence_breadth"]
    )
    assert bull["composite"] == float(src["composite"])
    assert set(bull["group_scores"]) == {
        g for g in bull["group_scores"] if src[f"gs_{g}"] == src[f"gs_{g}"]
    }
    assert bull["flags"]["bull_groups"] == src["bull_groups"].split(",")
    assert "group_pct" in bull["flags"] and "ev_insider_cluster" in bull["flags"]
    json.dumps([bull["group_scores"], bull["flags"]], allow_nan=False)
    # PackCandidate accepts the row as keyword arguments
    PackCandidate(**bull)


def test_requested_ticker_keeps_its_flag_and_a_failing_gate_is_listed():
    df = cross_section("2024-03-01", n=150, seed=3)
    df.loc[df["ticker"] == "T001", "dollar_volume_20d"] = 1.0  # fails liquidity
    screened = screen_cross_section(df, config=ScreenConfig(), requested=["T001"])
    rows = {r["ticker"]: r for r in pack_candidate_rows(screened, DAY)}
    assert rows["T001"]["bucket"] == "requested" and rows["T001"]["gate_ok"] is False
    assert rows["T001"]["flags"]["is_requested"] is True
    assert rows["T001"]["flags"]["gate_reasons"] == "low_liquidity"
    assert rows["T001"]["composite"] is None and rows["T001"]["group_scores"] == {}


def test_replace_deletes_the_session_and_adds_the_rows():
    session = MagicMock()
    rows = pack_candidate_rows(_screen(), DAY)
    assert replace_pack_candidates(session, DAY, rows) == len(rows)
    delete_sql = str(session.execute.call_args.args[0])
    assert delete_sql.startswith("DELETE FROM signals.pack_candidates")
    assert "session_date" in delete_sql
    assert session.add.call_count == len(rows) and session.flush.called
    assert isinstance(session.add.call_args.args[0], PackCandidate)
