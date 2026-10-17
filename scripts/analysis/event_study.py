"""Event study: do tickers earn more after a dated event than the universe?

For every event (ticker, event date) the position starting at ``open(d+1)`` -
``d`` = first session on/after the event date - is compared with the average
universe ticker on the same day.

Sources:
* ``--source index_add`` - additions to the S&P 500 / Nasdaq 100 from
  ``signals.index_membership`` (effective date; exploratory, two-sided).
* ``--source buyback --events-csv FILE`` - 8-K buyback authorizations
  collected by ``scripts/analysis/collect_buyback_events.py``.

Modes:
* ``--mode horizon`` (default, strategy-neutral, plan 2026-10-17 step 2) -
  forward returns ``return_20d`` / ``return_60d`` (``close(d+h) / open(d+1) -
  1``, no trade rule, no costs): raw, excess over the same-day universe mean
  and excess over SPY, with a batch-jackknife interval (batches of 2 x horizon
  event dates; markers only from 10 x horizon event dates). Decision
  rule (D1 only, analogous to the old one, exploratory because the buyback
  sample has already been inspected): >= 150 events, date-weighted mean return
  > 0 AND 95 % CI of (events - universe) at 20 sessions > 0.
* ``--mode barrier`` - the older +1 % / -2 % / 14-session trade (kept until
  the barrier label is deleted, plan step 6; delete the "Barrier mode" parts of
  this file then).

Read-only - nothing is written to the database.

Usage:
    .venv/bin/python scripts/analysis/event_study.py --source index_add \
        --out /app/repair_logs
    .venv/bin/python scripts/analysis/event_study.py --source buyback \
        --events-csv /app/repair_logs/buyback_events.csv --out /app/repair_logs
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from trading_signals.analysis.report_format import ci_cell
from trading_signals.analysis.report_format import pct as pct_signed
from trading_signals.analysis.stats import ci_supports_verdict
from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("analysis.event_study")

DELAYS = (0, 1, 2, 3, 5)
HORIZONS = (20, 60)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", choices=("index_add", "buyback"), required=True)
    p.add_argument("--mode", choices=("horizon", "barrier"), default="horizon",
                   help="horizon: 20/60-session returns (default); barrier: old trade")
    p.add_argument("--events-csv", default=None, help="Event file (source buyback)")
    p.add_argument("--start", type=date.fromisoformat, default=None,
                   help="First event / snapshot date (default: ml_start_date(), "
                        "resolved at run time)")
    p.add_argument("--min-gap-days", type=int, default=5,
                   help="Repeat events of a ticker within N calendar days are dropped")
    p.add_argument("--block", type=int, default=None,
                   help="Barrier mode only: bootstrap block in event dates "
                        "(default 20); the horizon mode uses a batch jackknife "
                        "with batches of 2 x horizon event dates")
    p.add_argument("--out", default="reports", help="Output directory")
    return p.parse_args(argv)


# ── Data loading (shared) ────────────────────────────────────────────


def load_sessions(session, start: date) -> list:
    from sqlalchemy import text

    return list(session.execute(text(
        "SELECT DISTINCT snapshot_date FROM signals.feature_snapshots "
        "WHERE snapshot_date >= :start ORDER BY 1"
    ), {"start": start}).scalars())


def load_index_adds(session, start: date) -> pd.DataFrame:
    from sqlalchemy import text

    df = pd.read_sql(text(
        "SELECT ticker, valid_from AS event_date, index_name AS grp "
        "FROM signals.index_membership "
        "WHERE source <> 'initial_seed' AND valid_from >= :start"
    ), session.bind, params={"start": start})
    return df


def load_buybacks(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["grp"] = np.where(df["with_earnings"].astype(bool),
                         "mit Quartalszahlen", "ohne Quartalszahlen")
    return df


def load_events(session, args: argparse.Namespace) -> pd.DataFrame | None:
    if args.source == "index_add":
        return load_index_adds(session, args.start)
    if not args.events_csv:
        logger.error("--events-csv is required for source buyback")
        return None
    return load_buybacks(args.events_csv)


# ── Horizon mode ─────────────────────────────────────────────────────


def load_returns(session, start: date) -> pd.DataFrame:
    """Forward returns of every snapshot row with liquidity (floats from SQL)."""
    from sqlalchemy import text

    return pd.read_sql(text(
        "SELECT snapshot_date, ticker, return_20d::float8 AS return_20d, "
        "return_60d::float8 AS return_60d "
        "FROM signals.feature_snapshots WHERE snapshot_date >= :start "
        "AND coalesce(dollar_volume_20d, 0) > 0"
    ), session.bind, params={"start": start})


def ci_s(ev: dict, key: str, digits: int = 2, horizon: int = 20) -> str:
    """Interval cell; the verdict marker needs 10 x horizon event dates."""
    return ci_cell(ev.get(key), ev.get(f"{key}_lo"), ev.get(f"{key}_hi"), digits,
                   mark=ci_supports_verdict(int(ev.get("days_with", 0)), horizon))


HORIZON_HEADER = [
    "| Gruppe | Ereignisse | Tage | Ø Rendite | Ø Universum "
    "| Überschuss vs. Universum (95 %-KI) | Überschuss vs. SPY (95 %-KI) "
    "| Trefferquote | Basisrate |",
    "|---|---|---|---|---|---|---|---|---|",
]


def horizon_row(name: str, ev: dict) -> str:
    if not ev.get("n_events"):
        return f"| {name} | 0 | 0 | – | – | – | – | – | – |"
    h = ev["horizon"]
    return (
        f"| {name} | {ev['n_events']} | {ev['days_with']} | "
        f"{pct_signed(ev['mean_ret'])} | {pct_signed(ev['uni_mean'])} | "
        f"{ci_s(ev, 'diff', 2, h)} | {ci_s(ev, 'diff_spy', 2, h)} | "
        f"{pct_signed(ev['hit_rate'], 1, False)} | "
        f"{pct_signed(ev['uni_hit_rate'], 1, False)} |"
    )


def run_horizon(args: argparse.Namespace) -> int:
    from trading_signals.analysis.benchmark import load_benchmark_returns
    from trading_signals.analysis.event_study import (
        align_events,
        dedupe_events,
        evaluate_events_horizon,
        event_selection,
        horizon_meta,
        passes_criterion_horizon,
    )
    from trading_signals.db.session import get_session

    with get_session() as session:
        returns = load_returns(session, args.start)
        sessions = load_sessions(session, args.start)
        spy = load_benchmark_returns(session, args.start, horizons=HORIZONS)
        raw = load_events(session, args)
    if raw is None:
        return 2

    meta = horizon_meta(returns, HORIZONS, spy)
    del returns
    last = meta.loc[meta["ret_20"].notna(), "snapshot_date"].max()
    raw = raw[pd.to_datetime(raw["event_date"]) >= pd.Timestamp(args.start)]
    events = dedupe_events(raw, args.min_gap_days)
    logger.info(f"{len(raw)} raw events -> {len(events)} after dedupe; "
                f"{len(meta)} snapshot rows, 20d returns until {last:%Y-%m-%d}")

    aligned = align_events(events, sessions)
    sel = event_selection(aligned, meta)
    title = {"index_add": "Index-Aufnahmen (D2, explorativ)",
             "buyback": "8-K-Rückkauf-Genehmigungen (D1)"}[args.source]
    lines = [
        f"# Event-Studie – {title} – Prognose-Horizonte",
        "",
        f"Erstellt {datetime.now():%Y-%m-%d %H:%M} · Rendite "
        "`close(d+h) / open(d+1) − 1` (h = 20, 60 Handelstage), Einstieg "
        "open(d+1), keine Handelsregel, keine Kosten",
        "",
        f"- Ereignisse roh: {len(raw)}, nach Entdoppelung ({args.min_gap_days} "
        f"Kalendertage): {len(events)}",
        f"- Mit Snapshot/Rendite-Zeile: {len(sel)} (ohne Snapshot: "
        f"{len(aligned) - len(sel)}; 20-Tage-Renditen bis {last:%Y-%m-%d}, "
        "jüngere Ereignisse sind noch offen)",
        "- Vergleich: Ø aller Universe-Ticker an denselben Tagen (Zeilen mit "
        "Liquidität > 0) und SPY; KI = Batch-Jackknife (Batches von 2 × Horizont "
        "Ereignistagen, t-Verteilung); Signifikanz-Marker erst ab 10 × Horizont "
        "Ereignistagen",
        "- Ereignisse mit überlappenden Fenstern (mehrere Ereignisse derselben "
        "Aktie innerhalb von 20/60 Tagen) werden nur über die Entdoppelung "
        "gefiltert",
        "",
    ]
    for h in HORIZONS:
        main_ev = evaluate_events_horizon(sel, meta, h)
        lines += [
            f"## Horizont {h} Handelstage", "",
            f"Hauptergebnis (Einstieg am Tag nach dem Ereignis; noch offen: "
            f"{main_ev.get('n_unrealised', 0)} Ereignisse)", "", *HORIZON_HEADER,
            horizon_row("**Alle Ereignisse**", main_ev),
        ]
        for grp, g in sel.groupby("grp", sort=True):
            lines.append(horizon_row(str(grp), evaluate_events_horizon(
                g, meta, h)))
        if main_ev.get("n_events"):
            lines += [
                "",
                f"Ereignis-gewichtet: Ø Rendite {pct_signed(main_ev['event_mean'])}, "
                f"Ø Überschuss {pct_signed(main_ev['event_ex_mean'])}, Trefferquote "
                f"{pct_signed(main_ev['event_hit_rate'], 1, False)}",
            ]
        if args.source == "buyback" and h == HORIZONS[0]:
            verdict = ("✅ bestanden" if passes_criterion_horizon(main_ev)
                       else "❌ nicht bestanden")
            lines += ["", "**Entscheidungsregel (≥ 150 Ereignisse, Ø Rendite > 0, "
                      f"KI der Differenz zum Universum > 0, 20 Tage): {verdict}** "
                      "(explorativ: dieselbe Stichprobe wurde schon mit der "
                      "Barrier-Regel gesehen)"]
        elif args.source == "index_add" and h == HORIZONS[0]:
            lines += ["", "_Explorativ (geringe Fallzahl, nur Wirksamkeitsdatum) – "
                      "keine Entscheidungsregel._"]

        lines += ["", f"### Verspäteter Einstieg ({h} Tage)", "", *HORIZON_HEADER]
        for k in DELAYS:
            ev = evaluate_events_horizon(
                event_selection(align_events(events, sessions, delay=k), meta),
                meta, h)
            lines.append(horizon_row(f"d + {k}", ev))
        lines += ["", f"### Je Jahr ({h} Tage)", "", *HORIZON_HEADER]
        years = sel["snapshot_date"].dt.year
        for y, g in sel.groupby(years, sort=True):
            lines.append(horizon_row(str(y), evaluate_events_horizon(
                g, meta, h, bootstrap=False)))
        lines.append("")

    return write_report(args, "\n".join(lines) + "\n", sel, suffix="horizon")


# ── Barrier mode (DEPRECATED - delete with the barrier label, step 6) ──


def load_labels(session, start: date) -> pd.DataFrame:
    from sqlalchemy import text

    return pd.read_sql(text(
        "SELECT snapshot_date, ticker, barrier_outcome::float8 AS barrier_outcome, "
        "barrier_ambiguous, return_barrier_14d::float8 AS return_barrier_14d "
        "FROM signals.feature_snapshots WHERE snapshot_date >= :start "
        "AND barrier_outcome IS NOT NULL AND coalesce(dollar_volume_20d, 0) > 0"
    ), session.bind, params={"start": start})


def pct(x, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    return f"{x * 100:.{digits}f} %"


def ci(ev: dict, key: str = "diff") -> str:
    if f"{key}_lo" not in ev:
        return "–"
    lo, hi = ev[f"{key}_lo"], ev[f"{key}_hi"]
    sig = "✅" if lo > 0 else ("❌" if hi < 0 else "n.s.")
    return f"{pct(ev[key], 3)} [{pct(lo, 3)} … {pct(hi, 3)}] {sig}"


HEADER = [
    "| Gruppe | Ereignisse | Tage | Trefferquote | Ø Netto | Ø Netto kons. "
    "| Ø Netto Universum | Differenz (95 %-KI) | Differenz kons. (95 %-KI) |",
    "|---|---|---|---|---|---|---|---|---|",
]


def row(name: str, ev: dict) -> str:
    if not ev.get("n_events"):
        return f"| {name} | 0 | 0 | – | – | – | – | – | – |"
    return (
        f"| {name} | {ev['n_events']} | {ev['days_with']} | {pct(ev['hit_rate'], 1)} | "
        f"{pct(ev['net'], 3)} | {pct(ev['net_cons'], 3)} | {pct(ev['uni_net'], 3)} | "
        f"{ci(ev)} | {ci(ev, 'diff_cons')} |"
    )


def run_barrier(args: argparse.Namespace) -> int:
    from trading_signals.analysis.event_study import (
        BREAK_EVEN_HIT_RATE,
        align_events,
        dedupe_events,
        evaluate_events,
        event_selection,
        label_meta,
        passes_criterion,
    )
    from trading_signals.db.session import get_session

    block = args.block or 20
    with get_session() as session:
        labels = load_labels(session, args.start)
        sessions = load_sessions(session, args.start)
        raw = load_events(session, args)
    if raw is None:
        return 2

    meta = label_meta(labels)
    del labels
    last_label = meta["snapshot_date"].max()
    raw = raw[pd.to_datetime(raw["event_date"]) >= pd.Timestamp(args.start)]
    events = dedupe_events(raw, args.min_gap_days)
    logger.info(f"{len(raw)} raw events → {len(events)} after dedupe; "
                f"{len(meta)} labelled trades, labels until {last_label:%Y-%m-%d}")

    aligned = align_events(events, sessions)
    sel = event_selection(aligned, meta)
    main_ev = evaluate_events(sel, meta, block=block)
    no_label = len(aligned) - len(sel)

    title = {"index_add": "Index-Aufnahmen (D2, explorativ)",
             "buyback": "8-K-Rückkauf-Genehmigungen (D1)"}[args.source]
    lines = [
        f"# Event-Studie – {title}",
        "",
        f"Erstellt {datetime.now():%Y-%m-%d %H:%M} · Trade +1 % / −2 % / "
        "14 Handelstage, Einstieg open(d+1), netto · Break-even-Trefferquote "
        f"{pct(BREAK_EVEN_HIT_RATE, 1)}",
        "",
        f"- Ereignisse roh: {len(raw)}, nach Entdoppelung "
        f"({args.min_gap_days} Kalendertage): {len(events)}",
        f"- Mit Trade-Label: {len(sel)} (ohne Label/Snapshot: {no_label}; "
        f"Labels bis {last_label:%Y-%m-%d})",
        "- Vergleich: Ø aller Universe-Trades an denselben Tagen; "
        "KI = Datumsblock-Bootstrap",
        "",
        "## Hauptergebnis (Einstieg am Tag nach dem Ereignis)",
        "",
        *HEADER,
        row("**Alle Ereignisse**", main_ev),
    ]
    for grp, g in sel.groupby("grp", sort=True):
        lines.append(row(str(grp), evaluate_events(g, meta, block=block)))
    lines += [
        "",
        f"Trade-gewichtet: Trefferquote {pct(main_ev.get('trade_hit_rate'), 1)}, "
        f"Ø Netto {pct(main_ev.get('trade_net'), 3)} "
        f"(konservativ {pct(main_ev.get('trade_net_cons'), 3)})",
    ]
    if args.source == "buyback":
        verdict = "✅ bestanden" if passes_criterion(main_ev) else "❌ nicht bestanden"
        lines += ["", "**Erfolgskriterium (≥ 150 Ereignisse, Ø Netto > 0, KI der "
                  f"Differenz > 0): {verdict}**"]
    else:
        lines += ["", "_Explorativ (geringe Fallzahl, nur Wirksamkeitsdatum) – kein "
                  "Erfolgskriterium._"]

    lines += ["", "## Verspäteter Einstieg (Gültigkeit des Signals)", "", *HEADER]
    for k in DELAYS:
        shifted = align_events(events, sessions, delay=k)
        ev = evaluate_events(event_selection(shifted, meta), meta, block=block)
        lines.append(row(f"d + {k}", ev))

    lines += ["", "## Je Jahr", "", *HEADER]
    years = sel["snapshot_date"].dt.year
    for y, g in sel.groupby(years, sort=True):
        lines.append(row(str(y), evaluate_events(g, meta, bootstrap=False)))

    return write_report(args, "\n".join(lines) + "\n", sel, suffix="barrier")


# ── Output ───────────────────────────────────────────────────────────


def write_report(args: argparse.Namespace, report: str, sel: pd.DataFrame,
                 suffix: str) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{date.today():%Y-%m-%d}"
    name = f"event_study_{args.source}" + ("" if suffix == "barrier" else f"_{suffix}")
    out_file = out_dir / f"{name}_{stamp}.md"
    out_file.write_text(report, encoding="utf-8")
    sel.to_csv(out_dir / f"{name}_trades_{stamp}.csv", index=False)
    print(report)
    logger.info(f"Report written to {out_file.resolve()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = parse_args(argv)
    if args.start is None:  # the retention window moves: resolve at run time
        from trading_signals.utils.retention import ml_start_date

        args.start = ml_start_date()
    return run_horizon(args) if args.mode == "horizon" else run_barrier(args)


if __name__ == "__main__":
    sys.exit(main())
