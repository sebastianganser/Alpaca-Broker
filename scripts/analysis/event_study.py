"""Event study on the barrier trade (concept §7.4, step D).

For every event (ticker, event date) the trade opened at ``open(d+1)`` –
``d`` = first session on/after the event date – is compared with the
average universe trade on the same day (+1 % / −2 % / 14 sessions, net of
costs, labels from ``feature_snapshots``).

Sources:
* ``--source index_add`` – additions to the S&P 500 / Nasdaq 100 from
  ``signals.index_membership`` (effective date; exploratory, two-sided).
* ``--source buyback --events-csv FILE`` – 8-K buyback authorizations
  collected by ``scripts/analysis/collect_buyback_events.py``.

Pre-registered success criterion (D1 only): ≥ 150 events, date-weighted
avg net > 0 AND 95 % date-block bootstrap CI of (events − universe) > 0.

Read-only – nothing is written to the database.

Usage:
    .venv/bin/python scripts/analysis/event_study.py --source index_add --out /app/repair_logs
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

from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("analysis.event_study")

DELAYS = (0, 1, 2, 3, 5)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", choices=("index_add", "buyback"), required=True)
    p.add_argument("--events-csv", default=None, help="Event file (source buyback)")
    p.add_argument("--start", type=date.fromisoformat, default=date(2022, 7, 1))
    p.add_argument("--min-gap-days", type=int, default=5,
                   help="Repeat events of a ticker within N calendar days are dropped")
    p.add_argument("--block", type=int, default=20, help="Bootstrap block (event dates)")
    p.add_argument("--out", default="reports", help="Output directory")
    return p.parse_args(argv)


# ── Data loading ─────────────────────────────────────────────────────


def load_labels(session, start: date) -> pd.DataFrame:
    from sqlalchemy import text

    return pd.read_sql(text(
        "SELECT snapshot_date, ticker, barrier_outcome::float8 AS barrier_outcome, "
        "barrier_ambiguous, return_barrier_14d::float8 AS return_barrier_14d "
        "FROM signals.feature_snapshots WHERE snapshot_date >= :start "
        "AND barrier_outcome IS NOT NULL AND coalesce(dollar_volume_20d, 0) > 0"
    ), session.bind, params={"start": start})


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


# ── Report helpers ───────────────────────────────────────────────────


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


# ── Main ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = parse_args(argv)

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

    with get_session() as session:
        labels = load_labels(session, args.start)
        sessions = load_sessions(session, args.start)
        if args.source == "index_add":
            raw = load_index_adds(session, args.start)
        else:
            if not args.events_csv:
                logger.error("--events-csv is required for source buyback")
                return 2
            raw = load_buybacks(args.events_csv)

    meta = label_meta(labels)
    del labels
    last_label = meta["snapshot_date"].max()
    raw = raw[pd.to_datetime(raw["event_date"]) >= pd.Timestamp(args.start)]
    events = dedupe_events(raw, args.min_gap_days)
    logger.info(f"{len(raw)} raw events → {len(events)} after dedupe; "
                f"{len(meta)} labelled trades, labels until {last_label:%Y-%m-%d}")

    aligned = align_events(events, sessions)
    sel = event_selection(aligned, meta)
    main_ev = evaluate_events(sel, meta, block=args.block)
    no_label = len(aligned) - len(sel)

    title = {"index_add": "Index-Aufnahmen (D2, explorativ)",
             "buyback": "8-K-Rückkauf-Genehmigungen (D1)"}[args.source]
    lines = [
        f"# Event-Studie – {title}",
        "",
        f"Erstellt {datetime.now():%Y-%m-%d %H:%M} · Trade +1 % / −2 % / 14 Handelstage, "
        f"Einstieg open(d+1), netto · Break-even-Trefferquote {pct(BREAK_EVEN_HIT_RATE, 1)}",
        "",
        f"- Ereignisse roh: {len(raw)}, nach Entdoppelung ({args.min_gap_days} Kalendertage): "
        f"{len(events)}",
        f"- Mit Trade-Label: {len(sel)} (ohne Label/Snapshot: {no_label}; "
        f"Labels bis {last_label:%Y-%m-%d})",
        "- Vergleich: Ø aller Universe-Trades an denselben Tagen; KI = Datumsblock-Bootstrap",
        "",
        "## Hauptergebnis (Einstieg am Tag nach dem Ereignis)",
        "",
        *HEADER,
        row("**Alle Ereignisse**", main_ev),
    ]
    for grp, g in sel.groupby("grp", sort=True):
        lines.append(row(str(grp), evaluate_events(g, meta, block=args.block)))
    lines += [
        "",
        f"Trade-gewichtet: Trefferquote {pct(main_ev.get('trade_hit_rate'), 1)}, "
        f"Ø Netto {pct(main_ev.get('trade_net'), 3)} "
        f"(konservativ {pct(main_ev.get('trade_net_cons'), 3)})",
    ]
    if args.source == "buyback":
        verdict = "✅ bestanden" if passes_criterion(main_ev) else "❌ nicht bestanden"
        lines += ["", "**Erfolgskriterium (≥ 150 Ereignisse, Ø Netto > 0, KI der Differenz > 0): "
                  f"{verdict}**"]
    else:
        lines += ["", "_Explorativ (geringe Fallzahl, nur Wirksamkeitsdatum) – kein "
                  "Erfolgskriterium._"]

    lines += ["", "## Verspäteter Einstieg (Gültigkeit des Signals)", "", *HEADER]
    for k in DELAYS:
        ev = evaluate_events(event_selection(align_events(events, sessions, delay=k), meta),
                             meta, block=args.block)
        lines.append(row(f"d + {k}", ev))

    lines += ["", "## Je Jahr", "", *HEADER]
    years = sel["snapshot_date"].dt.year
    for y, g in sel.groupby(years, sort=True):
        lines.append(row(str(y), evaluate_events(g, meta, bootstrap=False)))

    report = "\n".join(lines) + "\n"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"event_study_{args.source}_{date.today():%Y-%m-%d}.md"
    out_file.write_text(report, encoding="utf-8")
    sel.to_csv(out_dir / f"event_study_{args.source}_trades_{date.today():%Y-%m-%d}.csv",
               index=False)
    print(report)
    logger.info(f"Report written to {out_file.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
