"""Walk-forward test of the hit-probability model (concept §7.2, step B).

Fits fixed-parameter models (logistic regression, gradient boosting) per
quarter on all earlier data (purged), predicts P(take profit before stop)
out of sample, chooses the selection rule (model, max. candidates per day,
P threshold) on the DEVELOPMENT quarters only and then evaluates exactly
that rule once on the HOLDOUT quarters.

Success criterion (fixed in advance): holdout avg net return per trade > 0
AND the 95 % date-block bootstrap CI of (selection − universe) > 0.

Read-only – nothing is written to the database.

Usage (inside the container):
    .venv/bin/python scripts/analysis/walkforward_hit_model.py --out /app/repair_logs
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("analysis.walkforward_hit_model")

KS = (1, 3, 5)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dev-start", default="2023-07-01", help="First development test quarter")
    p.add_argument("--holdout-start", default="2025-01-01", help="First holdout test quarter")
    p.add_argument("--models", default="logit,gbm")
    p.add_argument("--max-train-rows", type=int, default=400_000)
    p.add_argument("--min-days", type=int, default=60,
                   help="Min. candidate days of a rule in the dev period")
    p.add_argument("--block", type=int, default=20, help="Bootstrap block (dates)")
    p.add_argument("--out", default="reports", help="Output directory")
    return p.parse_args(argv)


# ── Data loading ─────────────────────────────────────────────────────


def load_frame(session, start: date) -> tuple[pd.DataFrame, list[str]]:
    """Feature snapshots with label, all numeric columns cast to float in SQL."""
    from sqlalchemy import Boolean, text

    from trading_signals.db.models.features import FEATURE_COLUMNS, FeatureSnapshot

    existing = set(session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'signals' AND table_name = 'feature_snapshots'"
    )).scalars().all())
    table = FeatureSnapshot.__table__
    feats = [c for c in FEATURE_COLUMNS if c in existing]
    exprs = []
    for c in feats:
        cast = "::int::float8" if isinstance(table.c[c].type, Boolean) else "::float8"
        exprs.append(f"{c}{cast} AS {c}")
    sql = (
        "SELECT snapshot_date, ticker, barrier_outcome::float8 AS barrier_outcome, "
        "barrier_ambiguous, return_barrier_14d::float8 AS return_barrier_14d, "
        + ", ".join(exprs)
        + " FROM signals.feature_snapshots WHERE snapshot_date >= :start "
        "AND barrier_outcome IS NOT NULL AND coalesce(dollar_volume_20d, 0) > 0"
    )
    df = pd.read_sql(text(sql), session.bind, params={"start": start})
    for c in feats:
        df[c] = df[c].astype("float32")
    return df, feats


# ── Report helpers ───────────────────────────────────────────────────


def pct(x, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    return f"{x * 100:.{digits}f} %"


def ci(ev: dict, key: str = "diff") -> str:
    if key not in ev:
        return "–"
    lo, hi = ev[f"{key}_lo"], ev[f"{key}_hi"]
    sig = "✅" if lo > 0 else ("❌" if hi < 0 else "n.s.")
    return f"{pct(ev[key], 3)} [{pct(lo, 3)} … {pct(hi, 3)}] {sig}"


def ev_row(name: str, ev: dict) -> str:
    if not ev.get("n_trades"):
        return f"| {name} | 0 | 0 | 100 % | – | – | – | – | – |"
    return (
        f"| {name} | {ev['n_trades']} | {ev['days_with']} | {pct(ev['no_candidate_share'], 0)} | "
        f"{pct(ev['hit_rate'], 1)} | {pct(ev['net'], 3)} | {pct(ev['net_cons'], 3)} | "
        f"{pct(ev['uni_net'], 3)} | {ci(ev)} |"
    )


EV_HEADER = [
    "| Regel | Trades | Tage mit Kandidat | Tage ohne | Trefferquote | Ø Netto | Ø Netto kons. "
    "| Ø Netto Universum | Differenz (95 %-KI) |",
    "|---|---|---|---|---|---|---|---|---|",
]


def logit_coefficients(x: pd.DataFrame, meta: pd.DataFrame, before: pd.Timestamp,
                       purge: int, top: int = 15) -> list[tuple[str, float]]:
    """Standardised logit coefficients of a model fit on all data before ``before``."""
    from trading_signals.analysis.hit_model import make_model

    uniq = np.unique(meta["snapshot_date"].to_numpy(dtype="datetime64[ns]"))
    cut = int(np.searchsorted(uniq, np.datetime64(before, "ns"))) - purge
    if cut <= 0:
        return []
    mask = meta["snapshot_date"].isin(uniq[:cut]).to_numpy()
    model = make_model("logit")
    model.fit(x.loc[mask], meta["y"].to_numpy()[mask])
    coefs = model.named_steps["clf"].coef_[0]
    pairs = sorted(zip(x.columns, coefs), key=lambda t: -abs(t[1]))
    return [(c, float(v)) for c, v in pairs[:top]]


# ── Main ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")

    from trading_signals.analysis.hit_model import (
        BREAK_EVEN_HIT_RATE,
        PURGE_SESSIONS,
        Rule,
        build_design,
        calibration_table,
        choose_rule,
        evaluate_selection,
        quarter_splits,
        select_candidates,
        walk_forward_predict,
    )
    from trading_signals.db.session import get_session
    from trading_signals.utils.retention import ml_start_date

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    holdout_start = pd.Timestamp(args.holdout_start)

    with get_session() as session:
        df, feats = load_frame(session, ml_start_date())
    logger.info(f"{len(df)} labelled snapshots, {df['snapshot_date'].nunique()} dates, "
                f"{len(feats)} feature columns")
    if df.empty:
        logger.error("No labelled snapshots – abort")
        return 1

    x, meta = build_design(df, feats)
    del df
    logger.info(f"Design matrix {x.shape[0]} × {x.shape[1]}")
    splits = quarter_splits(meta["snapshot_date"], args.dev_start)
    logger.info(f"{len(splits)} walk-forward quarters: {[s[0] for s in splits]}")
    preds, folds = walk_forward_predict(x, meta, splits, models, args.max_train_rows)

    is_hold = preds["snapshot_date"] >= holdout_start
    dev, hold = preds[~is_hold], preds[is_hold]
    thresholds = sorted({0.0, 0.60, 0.65, round(BREAK_EVEN_HIT_RATE, 3), 0.70, 0.72, 0.75})
    rule, table = choose_rule(dev, models, KS, thresholds, min_days=args.min_days)

    lines = [
        "# Walk-forward-Test Trefferwahrscheinlichkeit (Schritt B)",
        "",
        f"Erstellt: {datetime.now():%Y-%m-%d %H:%M} · Daten {meta['snapshot_date'].min():%Y-%m-%d} – "
        f"{meta['snapshot_date'].max():%Y-%m-%d} · {len(meta)} Snapshots · {x.shape[1]} Eingaben",
        "",
        "Trade: Einstieg Open d+1, Ziel +1 %, Stop −2 %, max. 14 Handelstage, 0,05 % Kosten, "
        f"mehrdeutige Tage nach OHLC-Regel (*kons.* = als Stop gezählt). Break-even-Trefferquote "
        f"**{pct(BREAK_EVEN_HIT_RATE, 1)}**. Purge {PURGE_SESSIONS} Handelstage, quartalsweise neu trainiert.",
        "",
        f"Entwicklung: Testquartale ab {args.dev_start} bis vor {args.holdout_start} · "
        f"Holdout: ab {args.holdout_start} (einmalige Prüfung der in der Entwicklung gewählten Regel).",
        "",
        "## Modellgüte je Quartal (AUC, 0,5 = Zufall)",
        "",
        "*AUC gesamt* mischt Tages- und Aktienauswahl; *Tages-AUC* misst nur die Rangfolge "
        "der Aktien innerhalb eines Tages.",
        "",
        "| Quartal | " + " | ".join(f"AUC {m} | Tages-AUC {m}" for m in models)
        + " | Trefferquote Universum |",
        "|---|" + "---|---|" * len(models) + "---|",
    ]
    fdf = pd.DataFrame(folds)

    def _cells(g: pd.DataFrame) -> list[str]:
        cells = []
        for m in models:
            gm = g[g["model"] == m]
            cells += [f"{gm['auc'].mean():.3f}", f"{gm['daily_auc'].mean():.3f}"]
        return cells

    for q, g in fdf.groupby("quarter", sort=False):
        lines.append(f"| {q} | " + " | ".join(_cells(g)) + f" | {pct(g['base_rate'].iloc[0], 1)} |")
    for label, part in (("Entwicklung", fdf[fdf["quarter"] < str(pd.Period(holdout_start, 'Q'))]),
                        ("Holdout", fdf[fdf["quarter"] >= str(pd.Period(holdout_start, 'Q'))])):
        lines.append(f"| **Ø {label}** | " + " | ".join(_cells(part)) + " | |")

    lines += ["", "## Regelwahl auf der Entwicklungsphase", ""]
    shown = table[table["days_with"] > 0].copy()
    shown["label"] = shown["rule"].map(lambda r: r.label)
    shown = shown.sort_values("net", ascending=False).head(12)
    lines += [
        "| Regel | Trades | Tage mit Kandidat | Trefferquote | Ø Netto | Ø Netto Universum |",
        "|---|---|---|---|---|---|",
    ]
    for _, r in shown.iterrows():
        lines.append(f"| {r['label']} | {r['n_trades']} | {r['days_with']} | {pct(r['hit_rate'], 1)} | "
                     f"{pct(r['net'], 3)} | {pct(r['uni_net'], 3)} |")
    if rule is None:
        lines += ["", f"**Keine Regel mit mindestens {args.min_days} Kandidatentagen.**"]
        verdict = "nicht prüfbar"
    else:
        dev_ev = evaluate_selection(select_candidates(dev, rule), dev, block=args.block)
        hold_sel = select_candidates(hold, rule)
        hold_ev = evaluate_selection(hold_sel, hold, block=args.block)
        success = bool(hold_ev.get("n_trades")) and hold_ev["net"] > 0 and hold_ev["diff_lo"] > 0
        verdict = "✅ bestanden" if success else "❌ nicht bestanden"
        lines += [
            "", f"Gewählte Regel: **{rule.label}**", "",
            "## Ergebnis Holdout (entscheidend)", "", *EV_HEADER,
            ev_row("Entwicklung (In-Sample-Wahl)", dev_ev),
            ev_row("**Holdout**", hold_ev),
            "", f"Konservativ (mehrdeutig = Stop), Differenz zum Universum: {ci(hold_ev, 'diff_cons')}",
            "", f"**Erfolgskriterium (Ø Netto > 0 und KI der Differenz > 0): {verdict}**",
            "", "Holdout je Quartal:", "",
            "| Quartal | Trades | Tage mit Kandidat | Trefferquote | Ø Netto | Ø Netto Universum |",
            "|---|---|---|---|---|---|",
        ]
        for q, g in hold.groupby("quarter", sort=False):
            ev = evaluate_selection(select_candidates(g, rule), g, bootstrap=False)
            lines.append(f"| {q} | {ev['n_trades']} | {ev.get('days_with', 0)} | "
                         f"{pct(ev.get('hit_rate'), 1)} | {pct(ev.get('net'), 3)} | "
                         f"{pct(ev.get('uni_net'), 3)} |")
        lines += ["", f"### Kalibrierung Holdout ({rule.model})", "",
                  "| Ø P(Treffer) | Trefferquote | Ø Netto | Trades |", "|---|---|---|---|"]
        for _, r in calibration_table(hold, rule.model).iterrows():
            lines.append(f"| {pct(r['mean_p'], 1)} | {pct(r['hit_rate'], 1)} | "
                         f"{pct(r['net'], 3)} | {int(r['n'])} |")
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        hold_sel.to_csv(out_dir / f"hit_model_holdout_trades_{date.today():%Y-%m-%d}.csv",
                        index=False)

    lines += ["", "## Nur zur Information: Holdout Top-k ohne Schwelle", "", *EV_HEADER]
    for m in models:
        for k in (1, 5):
            r = Rule(m, k, 0.0)
            lines.append(ev_row(r.label, evaluate_selection(select_candidates(hold, r), hold,
                                                            block=args.block)))

    if "logit" in models:
        lines += ["", "## Logit-Koeffizienten (Training vor dem Holdout, standardisiert)", "",
                  "| Eingabe | Koeffizient |", "|---|---|"]
        for c, v in logit_coefficients(x, meta, holdout_start, PURGE_SESSIONS):
            lines.append(f"| {c} | {v:+.4f} |")

    lines += ["", f"Gesamturteil: **{verdict}**", ""]
    report = "\n".join(lines)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"hit_model_walkforward_{date.today():%Y-%m-%d}.md"
    out_file.write_text(report, encoding="utf-8")
    print(report)
    logger.info(f"Report written to {out_file.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
