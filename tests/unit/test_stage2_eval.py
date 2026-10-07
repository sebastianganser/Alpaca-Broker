"""Unit tests for analysis/stage2_eval.py (concept §7.7)."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from trading_signals.analysis.stage2_eval import (
    eligible_mask,
    evaluate_stage2,
    render_report,
)

DAYS = [date(2026, 10, 1) + timedelta(days=i) for i in range(4)]


def _snaps() -> pd.DataFrame:
    """4 days × 4 tickers. AAA always hits, BBB/CCC stop, DDD has ATR > 3 % (ineligible)."""
    rows = []
    for d in DAYS:
        for t, outcome, net, atr in [
            ("AAA", 1, 0.0095, 0.02),
            ("BBB", -1, -0.0205, 0.02),
            ("CCC", -1, -0.0205, 0.015),
            ("DDD", 1, 0.0095, 0.05),
        ]:
            rows.append({
                "snapshot_date": d, "ticker": t, "barrier_outcome": outcome,
                "return_barrier_14d": net, "barrier_ambiguous": False,
                "atr_14_pct": atr, "dollar_volume_20d": 1e9, "earnings_days_until": None,
            })
    return pd.DataFrame(rows)


def test_eligible_mask_uses_prefilter():
    s = _snaps()
    m = eligible_mask(s)
    assert m[s["ticker"] == "DDD"].eq(False).all()
    assert m[s["ticker"] != "DDD"].all()


def test_evaluate_buy_vs_eligible_and_rejected():
    reviews = pd.DataFrame({"session_date": DAYS, "on_time": [True, True, True, False]})
    dec = []
    for d, on_time in zip(DAYS, reviews["on_time"], strict=True):
        dec.append({"session_date": d, "ticker": "AAA", "action": "buy", "on_time": on_time})
        dec.append({"session_date": d, "ticker": "BBB", "action": "no_entry",
                    "on_time": on_time})
    res = evaluate_stage2(reviews, pd.DataFrame(dec), _snaps(), block=1, min_trades=100)

    main = res["buy_vs_eligible"]
    assert main["n_events"] == 3  # late day excluded
    assert main["trade_net"] > 0
    # eligible = AAA, BBB, CCC → mean (0.0095 - 0.041) / 3
    assert abs(main["uni_net"] - (0.0095 - 0.041) / 3) < 1e-9
    assert main["diff"] > 0
    assert res["late_buy_vs_eligible"]["n_events"] == 1
    assert res["no_entry_vs_eligible"]["trade_net"] < 0
    assert res["buy_minus_no_entry"]["diff"] > 0
    assert res["verdict"] == "vorläufig"
    assert res["n_reviews_on_time"] == 3


def test_verdict_after_min_trades():
    reviews = pd.DataFrame({"session_date": DAYS, "on_time": [True] * 4})
    dec = pd.DataFrame([
        {"session_date": d, "ticker": "AAA", "action": "buy", "on_time": True} for d in DAYS
    ])
    res = evaluate_stage2(reviews, dec, _snaps(), block=1, min_trades=4)
    assert res["verdict"] == "bestanden"
    dec["ticker"] = "BBB"
    res = evaluate_stage2(reviews, dec, _snaps(), block=1, min_trades=4)
    assert res["verdict"] == "nicht bestanden"


def test_pending_trades_without_label():
    snaps = _snaps()
    snaps.loc[snaps["snapshot_date"] == DAYS[-1], ["barrier_outcome", "return_barrier_14d"]] = None
    reviews = pd.DataFrame({"session_date": [DAYS[-1]], "on_time": [True]})
    dec = pd.DataFrame([{"session_date": DAYS[-1], "ticker": "AAA", "action": "buy",
                         "on_time": True}])
    res = evaluate_stage2(reviews, dec, snaps)
    assert res["buy_vs_eligible"]["n_events"] == 0
    assert res["buy_vs_eligible"]["n_pending"] == 1
    assert "offen 1" in render_report(res, date(2026, 10, 7))


def test_empty_inputs_render_hint():
    res = evaluate_stage2(
        pd.DataFrame(columns=["session_date", "on_time"]),
        pd.DataFrame(),
        pd.DataFrame(),
    )
    text = render_report(res, date(2026, 10, 7))
    assert "Noch keine `decisions.yaml`" in text


def test_report_contains_main_row():
    reviews = pd.DataFrame({"session_date": DAYS, "on_time": [True] * 4})
    dec = pd.DataFrame([
        {"session_date": d, "ticker": "AAA", "action": "buy", "on_time": True} for d in DAYS
    ])
    text = render_report(evaluate_stage2(reviews, dec, _snaps(), block=1), date(2026, 10, 7))
    assert "Käufe vs. geeignete Aktien" in text
    assert "Urteil: vorläufig" in text
    assert "| 2026-10-04 | AAA | Ziel |" in text
