"""Unit tests for the rank-based preliminary score of the Context Pack (M7)."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from trading_signals.derived import context_pack_generator as cpg
from trading_signals.derived.context_pack_generator import (
    PRELIMINARY_WEIGHTS,
    ContextPackGenerator,
    compute_preliminary_scores,
)

W = {"a": 0.5, "b": 0.3, "c": -0.2, "m": 1.0}


def _snaps():
    return [
        {"ticker": "T1", "a": 1.0, "b": 100.0, "c": 3.0, "m": 7.0},
        {"ticker": "T2", "a": 2.0, "b": 300.0, "c": 2.0, "m": 7.0},
        {"ticker": "T3", "a": 3.0, "b": 200.0, "c": 1.0, "m": 7.0},
        {"ticker": "T4", "a": 4.0, "b": None, "c": 0.0, "m": 7.0},
    ]


def _by_ticker(scored):
    return {s["ticker"]: s for s in scored}


def test_weights_are_provisional_and_cross_sectional_only():
    market_wide = ("macro_", "breadth_")
    assert not any(f.startswith(market_wide) for f in PRELIMINARY_WEIGHTS)


def test_constant_feature_gets_no_weight():
    scored = _by_ticker(compute_preliminary_scores(_snaps(), W))
    for s in scored.values():
        assert "m" not in s["score_contributions"]
    assert scored["T1"]["score_features_used"] == 3
    assert scored["T4"]["score_features_used"] == 2  # b missing
    # constant feature with huge weight does not shift scores
    without_m = _by_ticker(compute_preliminary_scores(
        _snaps(), {k: v for k, v in W.items() if k != "m"}))
    assert scored["T2"]["score"] == without_m["T2"]["score"]


def test_scale_invariance():
    base = compute_preliminary_scores(_snaps(), W)
    scaled_input = [
        dict(s, b=None if s["b"] is None else s["b"] * 1e6) for s in _snaps()
    ]
    scaled = compute_preliminary_scores(scaled_input, W)
    assert [s["score"] for s in base] == [s["score"] for s in scaled]


def test_rank_values_and_contributions_sum_to_score():
    scored = _by_ticker(compute_preliminary_scores(_snaps(), W, min_features=1))
    t3 = scored["T3"]
    # a: T3 is rank 3 of 4 -> pct (3-1)/3 = 2/3 ; centered 1/6
    assert t3["score_contributions"]["a"]["pct_rank"] == pytest.approx(2 / 3)
    assert t3["score_contributions"]["a"]["contribution"] == pytest.approx(0.5 / 6)
    total = sum(c["contribution"] for c in t3["score_contributions"].values())
    assert t3["score"] == pytest.approx(round(total, 6))
    # negative weight: lowest c (T4) gets the largest positive contribution
    t4_c = scored["T4"]["score_contributions"]["c"]
    assert t4_c["contribution"] == pytest.approx(0.1)


def test_ties_get_average_rank():
    snaps = [{"ticker": f"T{i}", "a": v} for i, v in enumerate([1.0, 1.0, 2.0])]
    scored = compute_preliminary_scores(snaps, {"a": 1.0}, min_features=1)
    assert scored[0]["score_contributions"]["a"]["pct_rank"] == pytest.approx(0.25)
    assert scored[2]["score_contributions"]["a"]["pct_rank"] == pytest.approx(1.0)


def test_min_contributing_threshold_and_no_mutation():
    snaps = _snaps()
    scored = _by_ticker(compute_preliminary_scores(snaps, W, min_features=3))
    assert scored["T4"]["score"] is None  # only a, c
    assert scored["T1"]["score"] is not None
    assert "score" not in snaps[0]


def test_bools_strings_and_nan_handled():
    snaps = [
        {"ticker": "T1", "a": True, "b": "x", "c": float("nan")},
        {"ticker": "T2", "a": False, "b": "y", "c": 1.0},
    ]
    scored = compute_preliminary_scores(snaps, {"a": 1.0, "b": 1.0, "c": 1.0},
                                        min_features=1)
    assert scored[0]["score_features_used"] == 1  # only bool a
    assert scored[0]["score"] > scored[1]["score"]


def test_generate_daily_ranks_by_rank_score(tmp_path, monkeypatch):
    snaps = []
    for i in range(10):
        snaps.append({
            "ticker": f"T{i}", "snapshot_date": date(2026, 10, 1),
            "feature_version": "2026.10-1",
            "price_vs_sma50": float(i),
            "relative_strength_spy": float(i) * 1e6,  # huge scale
            "rsi_14": 50.0 - i,
            "macro_vix_regime": 1.0,
            "dollar_volume_20d": 1e7,
        })
    gen = ContextPackGenerator(MagicMock(), output_dir=str(tmp_path))
    monkeypatch.setattr(gen, "_load_snapshots", lambda d: [dict(s) for s in snaps])
    monkeypatch.setattr(gen, "_get_universe_info", lambda t: {"sector": "Tech",
                                                              "industry": "X"})
    monkeypatch.setattr(gen, "_get_latest_price", lambda t, d: 100.0)
    n = gen.generate_daily(date(2026, 10, 1), top_n=3)
    assert n == 3
    files = sorted(p.name for p in (tmp_path / "2026-10-01").iterdir())
    assert files[1].endswith("T9.md")  # best: highest momentum, lowest RSI
    text = (tmp_path / "2026-10-01" / files[1]).read_text(encoding="utf-8-sig")
    assert "Perzentil-Rang" in text and "macro_vix_regime |" not in text.split(
        "## 2.")[1].split("## 3.")[0]


def test_load_snapshots_keeps_feature_version_string():
    row = MagicMock()
    for col in cpg.FeatureSnapshot.__table__.columns:
        setattr(row, col.name, None)
    row.ticker = "AAA"
    row.snapshot_date = date(2026, 10, 1)
    row.feature_version = "2026.10-1"
    row.rsi_14 = 55
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = [row]
    gen = ContextPackGenerator(session, output_dir="unused")
    (snap,) = gen._load_snapshots(date(2026, 10, 1))
    assert snap["feature_version"] == "2026.10-1"
    assert snap["rsi_14"] == 55.0 and isinstance(snap["rsi_14"], float)
    assert snap["snapshot_date"] == date(2026, 10, 1)
