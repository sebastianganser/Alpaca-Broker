"""Historical validation of the neutral screen (plan 2026-10-17, step 2).

"Validate the screen before go-live": apply the quality gate and the screen of
:mod:`trading_signals.analysis.screen` to every historical snapshot date and
compare the buckets with the gated universe on the strategy-neutral forward
returns ``return_20d`` / ``return_60d`` (``close(d+h) / open(d+1) - 1``):

* ``bull``   - top K by evidence breadth, then composite
* ``bear``   - bottom K (evaluated here even though the go-live config has it
  switched off)
* ``random`` - the deterministic random controls
* ``Universum`` - all gated tickers of the date

Every bucket is evaluated **absolute** (raw return) and **relative** (excess
over the same-day gated-universe mean and, if SPY returns are supplied, over
SPY), date-weighted (every date counts once), with a 95 % batch-jackknife
interval (batches of 2 x horizon dates, t critical value; h-day returns of
consecutive dates overlap and a date-block bootstrap was shown to be too
narrow, see ``analysis.criterion_sim``) and a Newey-West t-stat (lag h-1). A
verdict marker next to an interval is shown only from 10 x horizon dates with
data for that figure. The sign of the group definitions is NOT tuned on this
output (see the module docstring of ``analysis.screen``).

Pure functions (unit-tested with synthetic data); DB loading and file output
live in ``scripts/analysis/screen_backtest.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from typing import Any

import pandas as pd

from trading_signals.analysis.report_format import ci_cell as _ci
from trading_signals.analysis.report_format import num as _num
from trading_signals.analysis.report_format import pct as _pct
from trading_signals.analysis.screen import (
    BUCKET_BEAR,
    BUCKET_BULL,
    BUCKET_RANDOM,
    DEFAULT_CONFIG,
    ScreenConfig,
    screen_dates,
)
from trading_signals.analysis.stats import (
    batch_jackknife_diff,
    ci_supports_verdict,
    daily_rank_ic,
    has_batch_ci,
    ic_summary,
    mean_test,
    nw_lag_for_horizon,
    to_float,
)

DATE_COL = "snapshot_date"
HORIZONS = (20, 60)
#: Bucket label -> (screen bucket value or None for the universe, direction).
#: direction +1: a hit is a positive return, -1: a negative return.
BUCKETS: dict[str, tuple[str | None, int]] = {
    "Bullisch (Top)": (BUCKET_BULL, 1),
    "Bärisch (Bottom)": (BUCKET_BEAR, -1),
    "Zufalls-Kontrollen": (BUCKET_RANDOM, 1),
    "Universum (Gate)": (None, 1),
}
#: Minimum names on each side of a group's extreme-percentile spread.
MIN_GROUP_SIDE = 3
#: Groups whose literature sign was already seen to "work" in this project's own
#: 2026-10-17 feature analysis (insider buy ratio / value: t +3.0 / +2.8 against
#: return_60d, days-to-cover: t -3.4) on the very data of this backtest. A good
#: diagnosis for them is not independent evidence.
IN_SAMPLE_GROUPS = ("insider", "short_interest")
IN_SAMPLE_NOTE = (
    "† in-sample bezüglich der Feature-Analyse vom 2026-10-17: Insider-Käufe "
    "und Days-to-Cover waren dort auf denselben Daten bereits aufgefallen; "
    "ein gutes Ergebnis ist hier keine unabhängige Bestätigung."
)


def backtest_config(config: ScreenConfig = DEFAULT_CONFIG) -> ScreenConfig:
    """The go-live config with the bearish bucket switched on (symmetric test)."""
    return replace(config, bearish_enabled=True)


def _norm_dates(df: pd.DataFrame, col: str = DATE_COL) -> pd.DataFrame:
    out = df.copy()
    out[col] = pd.to_datetime(out[col]).dt.normalize()
    return out


def _diff_stats(a: pd.Series, b: pd.Series, horizon: int) -> dict[str, float]:
    """Mean of ``a - b`` (per-date series), jackknife CI and NW t.

    ``n_dates`` = dates in both series = the sample behind the interval (a
    verdict marker needs ``10 * horizon`` of them).
    """
    d = (a - b).dropna().sort_index()
    if d.empty:
        nan = float("nan")
        return {"diff": nan, "lo": nan, "hi": nan, "t_nw": nan, "n_dates": 0}
    t = mean_test(d, nw_lag_for_horizon(horizon))["t_nw"]
    if not has_batch_ci(len(d), horizon):  # fewer than two batches: no interval
        nan = float("nan")
        return {"diff": float(d.mean()), "lo": nan, "hi": nan, "t_nw": t,
                "n_dates": int(len(d))}
    diff, lo, hi = batch_jackknife_diff(a, b, horizon)
    return {"diff": diff, "lo": lo, "hi": hi, "t_nw": t, "n_dates": int(len(d))}


def evaluate_buckets(
    screened: pd.DataFrame,
    horizon: int,
    spy: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Compare the screen buckets with the gated universe for one horizon.

    Args:
        screened: Output of :func:`trading_signals.analysis.screen.screen_dates`
            with the forward-return columns kept.
        horizon: 20 or 60 (column ``return_<h>d``).
        spy: Optional frame indexed by date (or with a ``snapshot_date``
            column) holding ``return_<h>d`` of SPY.

    Returns:
        ``{"horizon", "n_dates", "independent_periods", "buckets": DataFrame,
        "spreads": DataFrame, "by_year": DataFrame}``. ``buckets`` has one row
        per bucket: names/day, raw mean, universe mean, excess over the
        universe and over SPY (mean, CI, NW t, ``*_n`` = dates behind the
        interval), hit rate vs base rate.
    """
    ret = f"return_{horizon}d"
    sub = screened[screened["gate_ok"] & screened[ret].notna()].copy()
    sub[DATE_COL] = pd.to_datetime(sub[DATE_COL]).dt.normalize()
    sub["pos"] = (sub[ret] > 0).astype(float)
    sub["neg"] = (sub[ret] < 0).astype(float)
    by_date = sub.groupby(DATE_COL)
    uni_mean = by_date[ret].mean()
    uni_pos, uni_neg = by_date["pos"].mean(), by_date["neg"].mean()
    n_dates = int(uni_mean.size)

    spy_ret = None
    if spy is not None and ret in spy.columns:
        spy_ret = (
            spy[ret] if isinstance(spy.index, pd.DatetimeIndex)
            else spy.set_index(DATE_COL)[ret]
        ).copy()
        spy_ret.index = pd.to_datetime(spy_ret.index).normalize()

    rows = []
    per_date: dict[str, pd.Series] = {}
    for label, (bucket, direction) in BUCKETS.items():
        part = sub if bucket is None else sub[sub["bucket"] == bucket]
        if part.empty:
            rows.append({"bucket": label, "n_obs": 0, "n_dates": 0})
            continue
        g = part.groupby(DATE_COL)
        m = g[ret].mean()
        per_date[label] = m
        hit = g["pos" if direction > 0 else "neg"].mean()
        base = uni_pos if direction > 0 else uni_neg
        ex = _diff_stats(m, uni_mean, horizon)
        edge = _diff_stats(hit, base, horizon)
        row = {
            "bucket": label, "n_obs": int(len(part)), "n_dates": int(m.size),
            "names_per_date": float(len(part) / m.size),
            "raw_mean": float(m.mean()),
            "uni_mean": float(uni_mean.reindex(m.index).mean()),
            "ex_uni": ex["diff"], "ex_uni_lo": ex["lo"], "ex_uni_hi": ex["hi"],
            "ex_uni_t": ex["t_nw"], "ex_uni_n": ex["n_dates"],
            "hit_rate": float(hit.mean()),
            "base_rate": float(base.reindex(hit.index).mean()),
            "hit_edge": edge["diff"], "hit_edge_lo": edge["lo"],
            "hit_edge_hi": edge["hi"], "hit_edge_n": edge["n_dates"],
        }
        if spy_ret is not None:
            sp = _diff_stats(m, spy_ret, horizon)
            row.update(ex_spy=sp["diff"], ex_spy_lo=sp["lo"], ex_spy_hi=sp["hi"],
                       ex_spy_t=sp["t_nw"], ex_spy_n=sp["n_dates"])
        rows.append(row)
    buckets = pd.DataFrame(rows)

    spreads = []
    pairs = [
        ("Bullisch minus Bärisch", "Bullisch (Top)", "Bärisch (Bottom)"),
        ("Bullisch minus Zufall", "Bullisch (Top)", "Zufalls-Kontrollen"),
        ("Zufall minus Universum", "Zufalls-Kontrollen", "Universum (Gate)"),
    ]
    for name, a, b in pairs:
        if a in per_date and b in per_date:
            st = _diff_stats(per_date[a], per_date[b], horizon)
            spreads.append({"spread": name, **st})
    spreads_df = pd.DataFrame(spreads)

    def _year_mean(series: pd.Series | None, year: int) -> float:
        if series is None:
            return float("nan")
        d = series.dropna()
        d = d[d.index.year == year]
        return float(d.mean()) if len(d) else float("nan")

    bull, bear = per_date.get("Bullisch (Top)"), per_date.get("Bärisch (Bottom)")
    year_rows = []
    for y in sorted(set(uni_mean.index.year)):
        row: dict[str, Any] = {"year": y,
                               "n_dates": int((uni_mean.index.year == y).sum())}
        for label in ("Bullisch (Top)", "Bärisch (Bottom)", "Zufalls-Kontrollen"):
            m = per_date.get(label)
            row[label] = _year_mean(None if m is None else m - uni_mean, y)
        if bull is not None and bear is not None:
            row["Bull minus Bear"] = _year_mean(bull - bear, y)
        year_rows.append(row)

    return {
        "horizon": horizon, "n_dates": n_dates,
        "independent_periods": n_dates / horizon if horizon else float("nan"),
        "has_spy": spy_ret is not None,
        "buckets": buckets, "spreads": spreads_df,
        "by_year": pd.DataFrame(year_rows),
    }


def group_diagnostics(
    screened: pd.DataFrame,
    horizon: int,
    config: ScreenConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """Per-group descriptive check of the a-priori signs (never used to tune).

    For every group and for ``composite`` / ``evidence_breadth``: mean daily
    rank IC of the oriented score vs the forward return (Newey-West t) and the
    spread of the bullish minus the bearish extreme percentile (per date mean
    return difference, NW t). A positive value means the literature sign held.
    """
    ret = f"return_{horizon}d"
    sub = screened[screened["gate_ok"] & screened[ret].notna()].copy()
    sub[DATE_COL] = pd.to_datetime(sub[DATE_COL]).dt.normalize()
    lag = nw_lag_for_horizon(horizon)
    rows = []
    targets = [(g.name, f"gs_{g.name}", f"gp_{g.name}") for g in config.groups]
    targets += [("composite", "composite", "composite_pct"),
                ("evidence_breadth", "evidence_breadth", None)]
    for name, score_col, pct_col in targets:
        ic = daily_rank_ic(sub, score_col, ret, DATE_COL, min_obs=20)
        s = ic_summary(ic, horizon) if len(ic) else {}
        row: dict[str, Any] = {
            "group": name, "n_dates": int(len(ic)),
            "mean_ic": s.get("mean_ic", float("nan")),
            "ic_t_nw": s.get("t_nw", float("nan")),
            "coverage": float(sub[score_col].notna().mean()),
        }
        if pct_col is not None:
            top = sub[sub[pct_col] >= config.bull_percentile]
            bot = sub[sub[pct_col] <= config.bear_percentile]
            both = top.groupby(DATE_COL)[ret].agg(["mean", "size"]).join(
                bot.groupby(DATE_COL)[ret].agg(["mean", "size"]),
                lsuffix="_top", rsuffix="_bot", how="inner",
            )
            both = both[(both["size_top"] >= MIN_GROUP_SIDE)
                        & (both["size_bot"] >= MIN_GROUP_SIDE)]
            spread = both["mean_top"] - both["mean_bot"]
            t = mean_test(spread, lag)
            row.update(spread=t["mean"], spread_t=t["t_nw"],
                       spread_dates=int(spread.size))
        rows.append(row)
    return pd.DataFrame(rows)


def coverage_tables(
    screened: pd.DataFrame, config: ScreenConfig = DEFAULT_CONFIG
) -> dict[str, pd.DataFrame]:
    """Per-year gate / bucket counts and group data coverage of gated rows."""
    df = screened.copy()
    df[DATE_COL] = pd.to_datetime(df[DATE_COL]).dt.normalize()
    df["year"] = df[DATE_COL].dt.year
    per_date = df.groupby(DATE_COL).agg(
        year=("year", "first"), rows=("ticker", "size"),
        gated=("gate_ok", "sum"),
        bull=("bucket", lambda s: int((s == BUCKET_BULL).sum())),
        bear=("bucket", lambda s: int((s == BUCKET_BEAR).sum())),
    )
    years = per_date.groupby("year").agg(
        dates=("rows", "size"), rows=("rows", "mean"), gated=("gated", "mean"),
        bull=("bull", "mean"), bear=("bear", "mean"),
        bull_full=("bull", lambda s: float((s >= config.k_bull).mean())),
    ).reset_index()
    gated = df[df["gate_ok"]]
    cov = {
        g.name: gated.groupby("year")[f"gs_{g.name}"].apply(lambda s: s.notna().mean())
        for g in config.groups
    }
    group_cov = pd.DataFrame(cov).reset_index() if cov else pd.DataFrame()
    reasons = (
        df.loc[~df["gate_ok"], "gate_reasons"].str.split(";").explode().value_counts()
    )
    return {"years": years, "group_coverage": group_cov,
            "gate_reasons": reasons.rename_axis("reason").reset_index(name="rows")}


def run_screen_backtest(
    features: pd.DataFrame,
    spy: pd.DataFrame | None = None,
    config: ScreenConfig | None = None,
    horizons: Sequence[int] = HORIZONS,
) -> dict[str, Any]:
    """Screen every date of ``features`` and evaluate the buckets.

    Args:
        features: ``feature_snapshots`` rows (``snapshot_date``, ``ticker``,
            scored features, ``dollar_volume_20d`` and ``return_<h>d``).
        spy: SPY forward returns (see :func:`evaluate_buckets`) or ``None``.
        config: Screen config (default: go-live config with bearish on).
    """
    config = config or backtest_config()
    feats = _norm_dates(features)
    keep = [f"return_{h}d" for h in horizons if f"return_{h}d" in feats.columns]
    for c in keep:
        feats[c] = to_float(feats[c])
    screened = screen_dates(feats, config, keep=keep)
    result: dict[str, Any] = {
        "config": config, "n_rows": int(len(screened)),
        "n_dates": int(screened[DATE_COL].nunique()) if len(screened) else 0,
        "first_date": screened[DATE_COL].min() if len(screened) else None,
        "last_date": screened[DATE_COL].max() if len(screened) else None,
        "horizons": {}, "coverage": coverage_tables(screened, config)
        if len(screened) else {},
        "screened": screened,
    }
    for h in horizons:
        if screened.empty or f"return_{h}d" not in screened.columns:
            continue
        result["horizons"][h] = {
            **evaluate_buckets(screened, h, spy),
            "groups": group_diagnostics(screened, h, config),
        }
    return result


# ── Report ───────────────────────────────────────────────────────────


def render_report(result: dict[str, Any], today: date | None = None) -> str:
    """German Markdown report of :func:`run_screen_backtest`."""
    cfg: ScreenConfig = result["config"]
    lines = [
        "# Screen-Backtest – neutraler Kandidaten-Screen",
        "",
        f"Erstellt {today or date.today():%Y-%m-%d} · Screen `screen/v1` · "
        f"{result['n_dates']} Handelstage "
        f"({_d(result['first_date'])} … {_d(result['last_date'])}) · "
        f"{result['n_rows']} Snapshot-Zeilen",
        "",
        "Zielgrößen: `return_h = close(d+h) / open(d+1) − 1` (h = 20, 60). Jeder "
        "Handelstag zählt einmal (datumsgewichtet). *Absolut* = Rohrendite, "
        "*relativ* = Überschuss gegenüber dem Mittel des Gate-Universums am "
        "selben Tag bzw. gegenüber SPY. KI = Batch-Jackknife (Batches von "
        "2 × Horizont Tagen, t-Verteilung), t = Newey-West (Lag h−1); "
        "Signifikanz-Marker erst ab 10 × Horizont Tagen mit Daten. Die "
        "Gruppen-Vorzeichen stammen aus der Literatur und werden anhand "
        "dieses Berichts **nicht** verändert (`docs/SCREEN.md`).",
        "",
        f"Konfiguration: K bullisch {cfg.k_bull}, K bärisch {cfg.k_bear} "
        f"(im Backtest immer ausgewertet), Zufallskontrollen {cfg.k_random}, "
        f"Perzentile ≥ {cfg.bull_percentile:.0%} / ≤ {cfg.bear_percentile:.0%}, "
        f"Breite ≥ {cfg.min_breadth_bull} bzw. ≤ {cfg.max_breadth_bear}, "
        f"Liquidität ≥ {cfg.min_dollar_volume / 1e6:.0f} Mio. USD, "
        f"≥ {cfg.min_price_sessions} Kurstage (Proxy: SMA50 vorhanden), "
        f"≥ {cfg.min_group_fraction:.0%} der Gruppen mit Daten.",
        "",
    ]
    for h, res in result["horizons"].items():
        lines += _horizon_section(h, res)
    cov = result.get("coverage") or {}
    if cov:
        lines += ["## Abdeckung", "", "Pro Jahr (Mittel je Handelstag):", "",
                  "| Jahr | Tage | Zeilen | Gate ok | Ø bullisch | Ø bärisch | "
                  "Tage mit voller Bullisch-Liste |",
                  "|---|---|---|---|---|---|---|"]
        for _, r in cov["years"].iterrows():
            lines.append(
                f"| {int(r['year'])} | {int(r['dates'])} | {r['rows']:.0f} | "
                f"{r['gated']:.0f} | {r['bull']:.1f} | {r['bear']:.1f} | "
                f"{_pct(r['bull_full'], 0, False)} |"
            )
        gc = cov["group_coverage"]
        if len(gc):
            names = [c for c in gc.columns if c != "year"]
            lines += ["", "Anteil der Gate-Zeilen mit Gruppen-Score je Jahr:", "",
                      "| Jahr | " + " | ".join(names) + " |",
                      "|---|" + "---|" * len(names)]
            for _, r in gc.iterrows():
                lines.append(f"| {int(r['year'])} | "
                             + " | ".join(_pct(r[n], 0, False) for n in names) + " |")
        gr = cov["gate_reasons"]
        if len(gr):
            lines += ["", "Gate-Ausschlüsse (Zeilen, Mehrfachgründe möglich): "
                      + ", ".join(f"{r['reason']} {int(r['rows'])}"
                                  for _, r in gr.iterrows()), ""]
    lines += [
        "",
        "---",
        "*Rückblick auf Altdaten: junge Quellen (Schätzungen, Optionen-IV, "
        "Sentiment, Short Volume) haben nur wenige Monate Historie und fehlen "
        "früher; die Gruppen-Abdeckung oben zeigt, was in welchem Jahr "
        "tatsächlich in den Screen einging. Keine Anlageberatung.*",
        "",
    ]
    return "\n".join(lines)


def _d(x: Any) -> str:
    return "–" if x is None else f"{pd.Timestamp(x):%Y-%m-%d}"


def _horizon_section(h: int, res: dict[str, Any]) -> list[str]:
    has_spy = bool(res["has_spy"])
    lines = [
        f"## Horizont {h} Handelstage",
        "",
        f"{res['n_dates']} Handelstage mit Rendite ≈ "
        f"{res['independent_periods']:.1f} unabhängige {h}-Tage-Perioden "
        f"(Batch {2 * h} Tage). Belastbar erst ab ≈ 10 unabhängigen Perioden; "
        f"Signifikanz-Marker erst ab {10 * h} Tagen mit Daten je Kennzahl.",
        "",
        "### Absolut und relativ",
        "",
        "| Gruppe | Namen/Tag | Ø Rendite | Ø Universum | Überschuss vs. Universum "
        "(95 %-KI) | t (NW) |" + (" Überschuss vs. SPY (95 %-KI) |" if has_spy else ""),
        "|---|---|---|---|---|---|" + ("---|" if has_spy else ""),
    ]
    for _, r in res["buckets"].iterrows():
        if not r.get("n_obs"):
            lines.append(f"| {r['bucket']} | 0 | – | – | – | – |"
                         + (" – |" if has_spy else ""))
            continue
        ex_cell = _ci(r["ex_uni"], r["ex_uni_lo"], r["ex_uni_hi"],
                      mark=_mark(r, "ex_uni_n", h))
        row = (f"| {r['bucket']} | {r['names_per_date']:.1f} | {_pct(r['raw_mean'])} | "
               f"{_pct(r['uni_mean'])} | {ex_cell} | {_num(r['ex_uni_t'])} |")
        if has_spy:
            spy_cell = _ci(r.get("ex_spy"), r.get("ex_spy_lo"), r.get("ex_spy_hi"),
                           mark=_mark(r, "ex_spy_n", h))
            row += f" {spy_cell} |"
        lines.append(row)
    lines += ["", "### Trefferquote gegen die Basisrate", "",
              "Bullisch/Zufall/Universum: Anteil Rendite > 0; bärisch: Anteil "
              "Rendite < 0. Basisrate = derselbe Anteil im Gate-Universum am "
              "selben Tag (querschnittlich: das Markt-Timing zählt 0).", "",
              "| Gruppe | Trefferquote | Basisrate | Differenz (95 %-KI) |",
              "|---|---|---|---|"]
    for _, r in res["buckets"].iterrows():
        if not r.get("n_obs"):
            continue
        edge_cell = _ci(r["hit_edge"], r["hit_edge_lo"], r["hit_edge_hi"], 2,
                        mark=_mark(r, "hit_edge_n", h))
        lines.append(
            f"| {r['bucket']} | {_pct(r['hit_rate'], 1, False)} | "
            f"{_pct(r['base_rate'], 1, False)} | {edge_cell} |"
        )
    if len(res["spreads"]):
        lines += ["", "### Spreads (je Tag, Differenz der Gruppenmittel)", "",
                  "| Spread | Tage | Differenz (95 %-KI) | t (NW) |",
                  "|---|---|---|---|"]
        for _, r in res["spreads"].iterrows():
            mark = ci_supports_verdict(int(r["n_dates"]), h)
            lines.append(
                f"| {r['spread']} | {int(r['n_dates'])} | "
                f"{_ci(r['diff'], r['lo'], r['hi'], mark=mark)} | "
                f"{_num(r['t_nw'])} |")
    by_year = res["by_year"]
    if len(by_year):
        cols = [c for c in by_year.columns if c not in ("year", "n_dates")]
        lines += ["", "### Je Jahr (Überschuss vs. Universum; Spread)", "",
                  "| Jahr | Tage | " + " | ".join(cols) + " |",
                  "|---|---|" + "---|" * len(cols)]
        for _, r in by_year.iterrows():
            lines.append(f"| {int(r['year'])} | {int(r['n_dates'])} | "
                         + " | ".join(_pct(r[c]) for c in cols) + " |")
    groups = res.get("groups")
    if groups is not None and len(groups):
        lines += ["", "### Gruppen-Diagnose (nur beschreibend – Vorzeichen werden "
                  "nicht angepasst)", "",
                  "Rang-IC des orientierten Scores und Spread oberes minus "
                  "unteres Extrem-Perzentil. Positiv = das Literatur-Vorzeichen "
                  "hat gehalten. 12 Gruppen × 2 Horizonte: ohne "
                  "Mehrfachtest-Korrektur nur Hinweise.", "",
                  "| Gruppe | Abdeckung | Tage | Ø IC | t (NW) | Spread | t (NW) |",
                  "|---|---|---|---|---|---|---|"]
        for _, r in groups.iterrows():
            label = r["group"] + (" †" if r["group"] in IN_SAMPLE_GROUPS else "")
            lines.append(
                f"| {label} | {_pct(r['coverage'], 0, False)} | "
                f"{int(r['n_dates'])} | {_num(r['mean_ic'], 4)} | "
                f"{_num(r['ic_t_nw'])} | "
                f"{_pct(r.get('spread'))} | {_num(r.get('spread_t'))} |"
            )
        lines += ["", IN_SAMPLE_NOTE]
    lines.append("")
    return lines


def _mark(row: pd.Series, key: str, horizon: int) -> bool:
    """Verdict marker only with ``10 * horizon`` dates behind the interval."""
    n = row.get(key)
    return n is not None and n == n and ci_supports_verdict(int(n), horizon)


__all__ = [
    "BUCKETS",
    "HORIZONS",
    "backtest_config",
    "coverage_tables",
    "evaluate_buckets",
    "group_diagnostics",
    "render_report",
    "run_screen_backtest",
]
