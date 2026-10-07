"""Forward test of stage 2 (Claude broker) – evaluation and report (concept §7.7).

Each ``buy`` decision is evaluated with the existing barrier label of the
snapshot (ticker, pack day d): entry ``open(d+1)``, +1 % / −2 % / 14
sessions, 0.05 % costs. Comparison on the same days with (a) all stocks
that pass the stage-1 risk pre-filter and (b) the whole universe.

Pre-registered criterion: at least :data:`MIN_TRADES_VERDICT` closed on-time
``buy`` trades, mean net > 0 and 95 % CI of the difference to (a) > 0.

* :func:`evaluate_stage2` – pure, unit-tested.
* :func:`load_stage2_frames` – DB loader.
* :func:`render_report` – German Markdown (``context_packs/stage2_auswertung.md``).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from trading_signals.analysis.barrier import block_bootstrap_diff
from trading_signals.analysis.event_study import (
    BREAK_EVEN_HIT_RATE,
    evaluate_events,
    event_selection,
    label_meta,
    passes_criterion,
)
from trading_signals.analysis.hit_model import DATE_COL
from trading_signals.derived.context_pack_generator import prefilter_reasons

MIN_TRADES_VERDICT = 100
REPORT_FILE = "stage2_auswertung.md"

SNAPSHOT_COLUMNS = (
    "snapshot_date", "ticker", "barrier_outcome", "return_barrier_14d",
    "barrier_ambiguous", "atr_14_pct", "dollar_volume_20d", "earnings_days_until",
)


def eligible_mask(snaps: pd.DataFrame) -> pd.Series:
    """Rows that pass the stage-1 risk pre-filter (same rule as the pack)."""
    if snaps.empty:
        return pd.Series(dtype=bool)
    cols = ["dollar_volume_20d", "atr_14_pct", "earnings_days_until"]
    records = snaps[cols].astype(object).where(snaps[cols].notna(), None).to_dict("records")
    return pd.Series([not prefilter_reasons(r) for r in records], index=snaps.index)


def _group(
    decisions: pd.DataFrame, meta: pd.DataFrame, compare: pd.DataFrame, block: int
) -> dict[str, Any]:
    keys = decisions[[DATE_COL, "ticker"]]
    if keys.empty or meta.empty:
        return {
            "n_events": 0, "n_trades": 0, "n_decisions": int(len(keys)),
            "n_pending": int(len(keys)),
            "trades": pd.DataFrame(columns=[DATE_COL, "ticker", "y", "net", "net_cons"]),
        }
    sel = event_selection(keys, meta)
    out = evaluate_events(sel, compare, block=block, bootstrap=not sel.empty)
    out["n_decisions"] = int(len(keys))
    out["n_pending"] = int(len(keys) - len(sel))
    out["trades"] = sel
    return out


def evaluate_stage2(
    reviews: pd.DataFrame,
    decisions: pd.DataFrame,
    snaps: pd.DataFrame,
    block: int = 20,
    min_trades: int = MIN_TRADES_VERDICT,
) -> dict[str, Any]:
    """Statistics of the stage-2 decisions (pure).

    Args:
        reviews: ``session_date, on_time`` (one row per reviewed pack day).
        decisions: ``session_date, ticker, action, on_time``.
        snaps: Snapshot rows of the reviewed days (:data:`SNAPSHOT_COLUMNS`).
    """
    snaps = snaps.copy()
    if not snaps.empty:
        snaps[DATE_COL] = pd.to_datetime(snaps["snapshot_date"])
    meta_all = label_meta(snaps) if not snaps.empty else pd.DataFrame(
        columns=[DATE_COL, "ticker", "y", "net", "net_cons"])
    meta_elig = (
        label_meta(snaps[eligible_mask(snaps)]) if not snaps.empty else meta_all.copy()
    )
    dec = decisions.reindex(columns=list(dict.fromkeys(
        ["session_date", "ticker", "action", "on_time", *decisions.columns]
    )))
    dec[DATE_COL] = pd.to_datetime(dec["session_date"])
    on_time = dec["on_time"].fillna(False).astype(bool)
    is_buy = dec["action"].eq("buy")
    buys = dec[is_buy & on_time]
    rejected = dec[~is_buy & on_time]
    late_buys = dec[is_buy & ~on_time]

    res: dict[str, Any] = {
        "n_reviews": int(len(reviews)),
        "n_reviews_on_time": int(reviews["on_time"].astype(bool).sum()) if len(reviews) else 0,
        "n_reviews_no_buy": int(
            (~reviews["session_date"].isin(dec.loc[is_buy, "session_date"])).sum()
        ) if len(reviews) else 0,
        "first_session": reviews["session_date"].min() if len(reviews) else None,
        "last_session": reviews["session_date"].max() if len(reviews) else None,
        "buy_vs_eligible": _group(buys, meta_all, meta_elig, block),
        "buy_vs_universe": _group(buys, meta_all, meta_all, block),
        "no_entry_vs_eligible": _group(rejected, meta_all, meta_elig, block),
        "late_buy_vs_eligible": _group(late_buys, meta_all, meta_elig, block),
        "min_trades": min_trades,
        "block": block,
    }
    # buy vs. no_entry on days where both exist (date-weighted, block bootstrap)
    b, r = res["buy_vs_eligible"]["trades"], res["no_entry_vs_eligible"]["trades"]
    if not b.empty and not r.empty:
        diff, lo, hi = block_bootstrap_diff(
            b.groupby(DATE_COL)["net"].mean(), r.groupby(DATE_COL)["net"].mean(), block=block
        )
        res["buy_minus_no_entry"] = {"diff": diff, "lo": lo, "hi": hi}

    main = res["buy_vs_eligible"]
    if main["n_events"] < min_trades:
        res["verdict"] = "vorläufig"
    else:
        res["verdict"] = (
            "bestanden" if passes_criterion(main, min_events=min_trades) else "nicht bestanden"
        )
    return res


def load_stage2_frames(session: Session) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """``(reviews, decisions, snaps)`` for :func:`evaluate_stage2` (read-only)."""
    conn = session.connection()
    reviews = pd.read_sql(text(
        "SELECT session_date, on_time, n_buy, n_no_entry FROM signals.stage2_reviews"
    ), conn)
    decisions = pd.read_sql(text("""
        SELECT d.session_date, d.ticker, d.action, d.pack_rank, r.on_time
        FROM signals.stage2_decisions d
        JOIN signals.stage2_reviews r USING (session_date)
    """), conn)
    if reviews.empty:
        return reviews, decisions, pd.DataFrame(columns=list(SNAPSHOT_COLUMNS))
    snaps = pd.read_sql(text(f"""
        SELECT {', '.join(SNAPSHOT_COLUMNS)}
        FROM signals.feature_snapshots
        WHERE snapshot_date IN (SELECT session_date FROM signals.stage2_reviews)
    """), conn)
    for c in ("return_barrier_14d", "atr_14_pct", "dollar_volume_20d", "earnings_days_until"):
        snaps[c] = pd.to_numeric(snaps[c], errors="coerce")
    return reviews, decisions, snaps


def _pct(x: Any, digits: int = 3) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "–"
    return "–" if v != v else f"{v * 100:+.{digits}f} %"


def _rate(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "–"
    return "–" if v != v else f"{v * 100:.1f} %"


def _ci(s: dict[str, Any], key: str = "diff") -> str:
    if s.get(key) is None:
        return "–"
    lo, hi = s.get(f"{key}_lo"), s.get(f"{key}_hi")
    sig = "n. s."
    if lo is not None and hi is not None:
        if lo > 0:
            sig = "✅"
        elif hi < 0:
            sig = "❌"
    return f"{_pct(s[key])} [{_pct(lo)} … {_pct(hi)}] {sig}"


def render_report(res: dict[str, Any], today: date) -> str:
    """German Markdown report of :func:`evaluate_stage2`."""
    main, uni = res["buy_vs_eligible"], res["buy_vs_universe"]
    lines = [
        f"# Vorwärtstest Stufe 2 – Stand {today.isoformat()}",
        "",
        "Regel: Einstieg zur Eröffnung nach dem Pack-Tag, Ziel +1 %, Stop −2 %, "
        f"max. 14 Handelstage, 0,05 % Kosten · Break-even-Trefferquote "
        f"{BREAK_EVEN_HIT_RATE * 100:.1f} % · Konzept §7.7",
        "",
    ]
    if not res["n_reviews"]:
        lines += [
            "Noch keine `decisions.yaml` eingelesen. Format: `docs/STAGE2_DECISIONS.md`.",
            "",
        ]
        return "\n".join(lines)

    n_closed = main["n_events"]
    verdict = res["verdict"]
    lines += [
        f"- Geprüfte Tage: {res['n_reviews']} ({res['first_session']} … "
        f"{res['last_session']}), davon rechtzeitig {res['n_reviews_on_time']}, "
        f"ohne Kauf {res['n_reviews_no_buy']}",
        f"- Käufe rechtzeitig: {main['n_decisions']} (abgeschlossen {n_closed}, "
        f"offen {main['n_pending']}) · Ablehnungen: "
        f"{res['no_entry_vs_eligible']['n_decisions']} · verspätete Käufe: "
        f"{res['late_buy_vs_eligible']['n_decisions']}",
        f"- **Urteil: {verdict}**"
        + (f" – {n_closed}/{res['min_trades']} abgeschlossene Käufe bis zur Prüfung"
           if verdict == "vorläufig" else ""),
        "",
        "## Ergebnis",
        "",
        "| Gruppe | Trades | Trefferquote | Ø Netto | Ø Netto kons. | Ø Vergleich | "
        "Differenz (95 %-KI) |",
        "|---|---|---|---|---|---|---|",
    ]

    min_days = 2 * res.get("block", 20)

    def row(name: str, s: dict[str, Any]) -> str:
        if not s["n_events"]:
            return f"| {name} | 0 | – | – | – | – | – |"
        diff = (
            _ci(s) if s.get("days_with", 0) >= min_days
            else f"{_pct(s.get('diff'))} (KI ab {min_days} Tagen)"
        )
        return (
            f"| {name} | {s['n_events']} | {_rate(s.get('trade_hit_rate'))} "
            f"| {_pct(s.get('trade_net'))} | {_pct(s.get('trade_net_cons'))} "
            f"| {_pct(s.get('uni_net'))} | {diff} |"
        )

    lines += [
        row("**Käufe vs. geeignete Aktien** (Hauptprüfung)", main),
        row("Käufe vs. Universum", uni),
        row("Abgelehnt (no_entry) vs. geeignete Aktien", res["no_entry_vs_eligible"]),
        row("Verspätete Käufe vs. geeignete Aktien", res["late_buy_vs_eligible"]),
        "",
        "Ø Netto/Trefferquote trade-gewichtet; Differenz und Vergleich je Tag gemittelt "
        f"(Datumsblock-Bootstrap, Block {res.get('block', 20)} Tage). Vorläufige Zahlen "
        "sind bei kleiner Fallzahl stark zufallsbehaftet.",
    ]
    bm = res.get("buy_minus_no_entry")
    if bm:
        lines += [
            "",
            f"Käufe minus Ablehnungen (Tage mit beiden): {_pct(bm['diff'])}"
            + (f" [{_pct(bm['lo'])} … {_pct(bm['hi'])}]"
               if main.get("days_with", 0) >= min_days else ""),
        ]

    trades = main["trades"]
    if not trades.empty:
        lines += ["", "## Letzte abgeschlossene Käufe", "",
                  "| Pack-Tag | Ticker | Ergebnis | Netto |", "|---|---|---|---|"]
        for _, t in trades.sort_values(DATE_COL).tail(15).iterrows():
            lines.append(
                f"| {t[DATE_COL]:%Y-%m-%d} | {t['ticker']} | "
                f"{'Ziel' if t['y'] == 1 else 'Stop/Zeit'} | {_pct(t['net'], 2)} |"
            )
    lines += ["", "---", "*Automatisch erzeugt (Nachtlauf `stage2_review`) · "
              "Keine Anlageberatung*", ""]
    return "\n".join(lines)


__all__ = [
    "MIN_TRADES_VERDICT",
    "REPORT_FILE",
    "eligible_mask",
    "evaluate_stage2",
    "load_stage2_frames",
    "render_report",
]
