"""Unit tests for the rank-based preliminary score of the Context Pack (M7)."""

import math
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


def _gen_snaps(n=10, **overrides):
    snaps = []
    for i in range(n):
        snap = {
            "ticker": f"T{i}", "snapshot_date": date(2026, 10, 1),
            "feature_version": "2026.10-1",
            "price_vs_sma50": float(i),
            "relative_strength_spy": float(i) * 1e6,  # huge scale
            "rsi_14": 50.0 - i,
            "macro_vix_regime": 1.0,
            "dollar_volume_20d": 1e7,
            "atr_14_pct": 0.02,
        }
        snap.update(overrides)
        snaps.append(snap)
    return snaps


def _generator(tmp_path, monkeypatch, snaps):
    gen = ContextPackGenerator(MagicMock(), output_dir=str(tmp_path))
    monkeypatch.setattr(gen, "_load_snapshots", lambda d: [dict(s) for s in snaps])
    monkeypatch.setattr(gen, "_get_universe_info", lambda t: {"sector": "Tech",
                                                              "industry": "X"})
    monkeypatch.setattr(gen, "_get_latest_price", lambda t, d: 100.0)
    return gen


def test_generate_daily_ranks_by_rank_score(tmp_path, monkeypatch):
    gen = _generator(tmp_path, monkeypatch, _gen_snaps())
    n = gen.generate_daily(date(2026, 10, 1), top_n=3)
    assert n == 3
    files = sorted(p.name for p in (tmp_path / "2026-10-01").iterdir())
    assert files[1].endswith("T9.md")  # best: highest momentum, lowest RSI
    text = (tmp_path / "2026-10-01" / files[1]).read_text(encoding="utf-8-sig")
    assert "Perzentil-Rang" in text and "macro_vix_regime |" not in text.split(
        "## 2.")[1].split("## 3.")[0]
    assert "ranking_validated: false" in text


@pytest.mark.parametrize("overrides, reason", [
    ({}, None),
    ({"atr_14_pct": 0.005}, "ATR < 1 %"),
    ({"atr_14_pct": 0.035}, "ATR > 3 %"),
    ({"atr_14_pct": None}, "ATR fehlt"),
    ({"dollar_volume_20d": 0.0}, "keine Liquidität"),
    ({"earnings_days_until": 0}, "Earnings im Haltefenster"),
    ({"earnings_days_until": 21}, "Earnings im Haltefenster"),
    ({"earnings_days_until": 22}, None),
    ({"earnings_days_until": -3}, None),  # stale date in the past
    ({"earnings_days_until": None}, None),  # unknown -> allowed
    ({"atr_14_pct": 0.01}, None),  # bounds inclusive
    ({"atr_14_pct": 0.03}, None),
])
def test_prefilter_reasons(overrides, reason):
    snap = {"dollar_volume_20d": 1e7, "atr_14_pct": 0.02, **overrides}
    reasons = cpg.prefilter_reasons(snap)
    assert reasons == ([] if reason is None else [reason])


def test_prefilter_matches_barrier_backtest_definition():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[2] / "scripts" / "analysis" / "backtest_barrier.py"
    spec = importlib.util.spec_from_file_location("backtest_barrier", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.FILTER_ATR_MIN == cpg.FILTER_ATR_MIN
    assert mod.FILTER_ATR_MAX == cpg.FILTER_ATR_MAX
    assert int(math.ceil(14 * 7 / 5)) + 1 == cpg.EARNINGS_BLOCK_CAL_DAYS


def test_generate_daily_skips_ineligible(tmp_path, monkeypatch):
    snaps = _gen_snaps()
    snaps[9]["atr_14_pct"] = 0.05  # best score, but too volatile
    snaps[8]["earnings_days_until"] = 5  # earnings inside the window
    gen = _generator(tmp_path, monkeypatch, snaps)
    assert gen.generate_daily(date(2026, 10, 1), top_n=2) == 2
    files = sorted(p.name for p in (tmp_path / "2026-10-01").iterdir())
    assert files == ["00_uebersicht.md", "01_T7.md", "02_T6.md"]
    overview = (tmp_path / "2026-10-01" / "00_uebersicht.md").read_text(
        encoding="utf-8-sig")
    assert "Geeignet nach Risiko-Vorfilter: 8" in overview
    assert "| ATR > 3 % | 1 |" in overview


def test_generate_daily_no_candidates_writes_overview(tmp_path, monkeypatch):
    day_dir = tmp_path / "2026-10-01"
    day_dir.mkdir()
    (day_dir / "01_OLD.md").write_text("stale", encoding="utf-8")
    gen = _generator(tmp_path, monkeypatch, _gen_snaps(atr_14_pct=0.06))
    assert gen.generate_daily(date(2026, 10, 1)) == 0
    files = sorted(p.name for p in day_dir.iterdir())
    assert files == ["00_uebersicht.md"]  # stale candidate file removed
    overview = (day_dir / "00_uebersicht.md").read_text(encoding="utf-8-sig")
    assert "Heute keine Kandidaten" in overview
    assert "| ATR > 3 % | 10 |" in overview
    assert "VIX Regime | Medium" in overview  # market context still shown


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


# ── Index-addition warning + stage-2 feedback (concept §7.7) ──


def test_index_addition_text():
    d = date(2026, 10, 6)
    assert cpg.index_addition_text([("sp500", date(2026, 10, 1))], d) == (
        "S&P 500 wirksam 2026-10-01 (vor 5 Tagen)"
    )
    assert cpg.index_addition_text([("nasdaq100", d)], d).endswith("(heute)")
    assert cpg.index_addition_text([("sp500", date(2026, 10, 9))], d).endswith("(in 3 Tagen)")
    sync = cpg.index_addition_text([("sp500", date(2026, 10, 1), "index_sync")], d)
    assert sync.startswith("S&P 500 aufgenommen, erkannt 2026-10-01")
    assert "genaues Datum unbekannt" in sync


def test_decisions_template_lists_top_tickers():
    lines = cpg.decisions_template(date(2026, 10, 6), ["NVDA", "MSFT"])
    assert lines[0] == "schema: stage2-decisions/v1"
    assert "session: 2026-10-06" in lines
    assert "  - ticker: MSFT" in lines
    assert cpg.decisions_template(date(2026, 10, 6), [])[-1] == "decisions: []"


def test_generate_daily_marks_fresh_index_additions(tmp_path, monkeypatch):
    gen = _generator(tmp_path, monkeypatch, _gen_snaps())
    monkeypatch.setattr(gen, "_recent_index_additions",
                        lambda d: {"T9": [("sp500", date(2026, 9, 28))],
                                   "XYZ": [("nasdaq100", date(2026, 9, 30))]})
    gen.generate_daily(date(2026, 10, 1), top_n=2)
    day = tmp_path / "2026-10-01"
    cand = (day / "01_T9.md").read_text(encoding="utf-8-sig")
    assert 'index_added: "S&P 500 wirksam 2026-09-28 (vor 3 Tagen)"' in cand
    assert "⚠️ **Index-Aufnahme:**" in cand
    assert "index_added" not in (day / "02_T8.md").read_text(encoding="utf-8-sig")
    overview = (day / "00_uebersicht.md").read_text(encoding="utf-8-sig")
    assert "## Warnhinweis Index-Aufnahme" in overview
    assert "- **T9**: S&P 500" in overview
    assert "XYZ" not in overview  # not in the universe/eligible set


def test_overview_contains_stage2_feedback_section(tmp_path, monkeypatch):
    gen = _generator(tmp_path, monkeypatch, _gen_snaps())
    gen.generate_daily(date(2026, 10, 1), top_n=2)
    overview = (tmp_path / "2026-10-01" / "00_uebersicht.md").read_text(encoding="utf-8-sig")
    assert "## Rückmeldung Stufe 2" in overview
    assert "## Warnhinweis Index-Aufnahme" not in overview
    yaml_block = overview.split("```yaml\n")[1].split("```")[0]
    assert "session: 2026-10-01" in yaml_block
    assert "  - ticker: T9" in yaml_block and "  - ticker: T8" in yaml_block
