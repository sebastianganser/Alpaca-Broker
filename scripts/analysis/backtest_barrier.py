"""Barrier backtest of the daily candidate selection (concept K1).

Question: would the daily top-N candidates of the CURRENT context-pack score
have reached the take profit before the stop more often than the universe
and than chance (b/(a+b)) – after costs?

For every parameter combination (TP × SL multiple, max holding days) this
prints / writes a Markdown report with

* groups: universe, top N, bottom N (sanity check), top N filtered
  (ATR 1–3 %, no earnings within the holding window), top N filtered in a
  favourable market regime (SPY > SMA200 and VIX < 25)
* hit rate, random hit rate, avg net return/trade, time-stop share, holding days
* date-block bootstrap CI of (top N − universe) avg net return
* per-year split

Read-only – nothing is written to the database.

Usage (inside the container):
    .venv/bin/python scripts/analysis/backtest_barrier.py
    .venv/bin/python scripts/analysis/backtest_barrier.py --tp 0.005,0.01 --sl-mult 2,3 --max-days 14
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trading_signals.utils.logging import get_logger, setup_logging

logger = get_logger("analysis.backtest_barrier")

FILTER_ATR_MIN = 0.01
FILTER_ATR_MAX = 0.03
REGIME_VIX_MAX = 25.0
SMA_REGIME = 200

# ── Frozen copy of the retired context-pack score (plan 2026-10-17, step 3) ──
# The hand-weighted score left the context pack with pack format 2. The K1
# barrier backtest still reproduces its report with this copy; it is removed
# together with this script in plan step 6.

# PROVISIONAL score weights — NOT validated.
# Originally taken from the Sprint 9 ML analysis top features, but that
# analysis was biased (pooled correlations, row-iid tests, leaky CV; see
# review finding H4). Treat these purely as a hand-tuned placeholder until
# the rebuilt analysis (daily rank IC / purged CV) or R1/F1 provide
# validated importances.
#
# Weights are applied to per-date cross-sectional percentile ranks
# (centered: pct_rank - 0.5), never to raw values, so the scale of a
# feature does not matter. Market-wide features (macro_*, breadth_*) were
# removed: they are identical for all tickers on a date and cannot rank
# candidates (they would be skipped automatically anyway).
PRELIMINARY_WEIGHTS = {
    "analyst_rating_score": 0.15,
    "insider_net_buy_count_30d": 0.12,
    "ark_conviction_score": 0.10,
    "price_vs_sma50": 0.10,
    "sentiment_momentum": 0.08,
    "sue_last": 0.08,
    "rsi_14": -0.07,  # negative = oversold is bullish
    "relative_strength_spy": 0.10,
    "sector_relative_return_20d": 0.08,
    "volume_ratio_20d": 0.04,
}

#: Minimum number of contributing (non-constant, non-null) features.
MIN_CONTRIBUTING_FEATURES = 3


def _numeric(val: Any) -> float | None:
    """Float value of a snapshot entry (bool -> 0/1), None if not numeric."""
    if val is None or isinstance(val, (str, date)):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def compute_preliminary_scores(
    snapshots: list[dict],
    weights: dict[str, float] | None = None,
    min_features: int = MIN_CONTRIBUTING_FEATURES,
) -> list[dict]:
    """Rank-based preliminary score for one date's snapshots (pure function).

    For every weighted feature the cross-sectional percentile rank over the
    snapshots that have a value is computed (average ties,
    ``(rank-1)/(n-1)``) and centered (``- 0.5`` -> range [-0.5, 0.5]).
    The score is ``sum(weight * centered_rank)`` over the features a ticker
    has; missing features count as neutral (0).

    Features with fewer than 2 distinct values on that date (e.g.
    market-wide features, or all-equal values) carry no information and get
    NO weight (they do not count as contributing).

    Args:
        snapshots: Feature dicts of ONE snapshot date (must contain "ticker").
        weights: Feature -> weight (default :data:`PRELIMINARY_WEIGHTS`).
        min_features: Tickers with fewer contributing features get
            ``score = None``.

    Returns:
        New dicts (input is not mutated) with ``score``,
        ``score_features_used`` and ``score_contributions``
        (``{feature: {value, pct_rank, weight, contribution}}``).
    """
    weights = PRELIMINARY_WEIGHTS if weights is None else weights
    out = [dict(s) for s in snapshots]

    # feature -> {row index: centered pct rank}
    ranks: dict[str, dict[int, float]] = {}
    for feature in weights:
        vals = [(i, _numeric(s.get(feature))) for i, s in enumerate(out)]
        vals = [(i, v) for i, v in vals if v is not None]
        if len({v for _, v in vals}) < 2:
            continue  # constant / empty on this date -> no weight
        sorted_vals = sorted(v for _, v in vals)
        n = len(sorted_vals)
        # average rank for ties (1-based)
        first_pos: dict[float, int] = {}
        counts: dict[float, int] = {}
        for pos, v in enumerate(sorted_vals, start=1):
            first_pos.setdefault(v, pos)
            counts[v] = counts.get(v, 0) + 1
        ranks[feature] = {
            i: ((first_pos[v] + (counts[v] - 1) / 2.0) - 1.0) / (n - 1) - 0.5
            for i, v in vals
        }

    for i, snap in enumerate(out):
        contributions: dict[str, dict[str, float]] = {}
        score = 0.0
        for feature, weight in weights.items():
            r = ranks.get(feature, {}).get(i)
            if r is None:
                continue
            contrib = weight * r
            score += contrib
            contributions[feature] = {
                "value": _numeric(snap.get(feature)),
                "pct_rank": r + 0.5,
                "weight": weight,
                "contribution": contrib,
            }
        enough = len(contributions) >= min_features
        snap["score"] = round(score, 6) if enough else None
        snap["score_features_used"] = len(contributions)
        snap["score_contributions"] = contributions
    return out


def parse_floats(value: str) -> list[float]:
    return [float(v) for v in value.split(",") if v.strip()]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--start", type=date.fromisoformat, default=None,
                   help="First signal date (default: ml_start_date())")
    p.add_argument("--end", type=date.fromisoformat, default=None,
                   help="Last signal date (default: all)")
    p.add_argument("--tp", default="0.005,0.0075,0.01",
                   help="Take-profit levels as fractions")
    p.add_argument("--sl-mult", default="2,3", help="Stop = multiple × TP")
    p.add_argument("--max-days", type=int, default=14, help="Time stop (sessions)")
    p.add_argument("--cost", type=float, default=0.0005, help="Round-trip cost")
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--block", type=int, default=20, help="Bootstrap block (dates)")
    p.add_argument("--out", default="reports", help="Output directory")
    return p.parse_args(argv)


# ── Data loading ─────────────────────────────────────────────────────────

def load_features(session, start: date, end: date | None) -> pd.DataFrame:
    from sqlalchemy import text

    wanted = [*PRELIMINARY_WEIGHTS, "dollar_volume_20d", "atr_14_pct",
              "earnings_days_until", "macro_vix"]
    existing = set(session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'signals' AND table_name = 'feature_snapshots'"
    )).scalars().all())
    cols = [c for c in dict.fromkeys(wanted) if c in existing]
    missing = sorted(set(wanted) - existing)
    if missing:
        logger.warning(f"feature_snapshots lacks columns: {missing}")
    sql = (
        f"SELECT snapshot_date, ticker, {', '.join(cols)} "
        "FROM signals.feature_snapshots WHERE snapshot_date >= :start"
        + (" AND snapshot_date <= :end" if end else "")
    )
    params = {"start": start, **({"end": end} if end else {})}
    df = pd.read_sql(text(sql), session.bind, params=params)
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    return df


def load_prices(session, start: date) -> pd.DataFrame:
    from sqlalchemy import text

    df = pd.read_sql(text(
        "SELECT ticker, trade_date, open, high, low, close "
        "FROM signals.prices_daily "
        "WHERE trade_date >= :start AND NOT coalesce(is_extrapolated, false)"
    ), session.bind, params={"start": start})
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


# ── Selection ────────────────────────────────────────────────────────────

def score_all(features: pd.DataFrame) -> pd.DataFrame:
    """Context-pack score per date (same function as production)."""
    parts = []
    for d, g in features.groupby("snapshot_date", sort=True):
        recs = g.to_dict("records")
        scored = compute_preliminary_scores(recs)
        parts.append(pd.DataFrame({
            "snapshot_date": d,
            "ticker": [s["ticker"] for s in scored],
            "score": [s["score"] for s in scored],
        }))
    scores = pd.concat(parts, ignore_index=True)
    return features.merge(scores, on=["snapshot_date", "ticker"], how="left")


def tradable(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["score"].notna() & (df["dollar_volume_20d"].fillna(0) > 0)]


def top_n(df: pd.DataFrame, n: int, ascending: bool = False) -> pd.DataFrame:
    return (df.sort_values(["snapshot_date", "score"], ascending=[True, ascending])
              .groupby("snapshot_date", sort=False).head(n))


def filtered(df: pd.DataFrame, max_days: int) -> pd.DataFrame:
    """ATR fits the barriers and no earnings inside the holding window."""
    atr_ok = df["atr_14_pct"].between(FILTER_ATR_MIN, FILTER_ATR_MAX)
    # max_days sessions ≈ max_days * 7/5 calendar days (+1 for the entry gap)
    window_cal = int(np.ceil(max_days * 7 / 5)) + 1
    if "earnings_days_until" in df.columns:
        earn = df["earnings_days_until"]
        earn_ok = earn.isna() | (earn < 0) | (earn > window_cal)
    else:
        earn_ok = True
    return df[atr_ok & earn_ok]


def regime_dates(features: pd.DataFrame, prices: pd.DataFrame) -> set:
    """Signal dates with SPY close > SMA200 and VIX < REGIME_VIX_MAX."""
    spy = prices[prices["ticker"] == "SPY"].sort_values("trade_date")
    if len(spy) < SMA_REGIME:
        logger.warning("No SPY history – regime filter disabled")
        return set()
    spy = spy.assign(sma=spy["close"].rolling(SMA_REGIME).mean())
    trend_ok = set(spy.loc[spy["close"] > spy["sma"], "trade_date"])
    if "macro_vix" in features.columns:
        vix = features.groupby("snapshot_date")["macro_vix"].median()
        vix_ok = set(vix[vix < REGIME_VIX_MAX].index)
    else:
        vix_ok = set(features["snapshot_date"].unique())
    return trend_ok & vix_ok


# ── Report ───────────────────────────────────────────────────────────────

def fmt_pct(x: float | None, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    return f"{x * 100:.{digits}f} %"


def per_date_net(trades: pd.DataFrame) -> pd.Series:
    return trades.groupby("snapshot_date")["net_return"].mean()


MODES = {"stop": "konservativ", "ohlc": "OHLC-Pfad"}


def run_combo(p, universe: dict[str, pd.DataFrame], groups: dict[str, pd.DataFrame],
              block: int) -> list[str]:
    """Report for one TP/SL combination; ``universe`` maps mode → trades."""
    from trading_signals.analysis.barrier import summarize
    from trading_signals.analysis.stats import block_bootstrap_diff

    key = ["snapshot_date", "ticker"]
    lines = [f"## {p.label}", "",
             f"Zufalls-Trefferquote (Break-even brutto): **{fmt_pct(p.random_hit_rate, 1)}**", "",
             "| Gruppe | Trades | Treffer kons. | Treffer OHLC | Ø Netto kons. | Ø Netto OHLC "
             "| mehrdeutig | Zeitstopp | Ø Haltedauer |",
             "|---|---|---|---|---|---|---|---|---|"]
    subsets: dict[str, dict[str, pd.DataFrame]] = {}
    for mode, uni in universe.items():
        subsets[mode] = {"Universum": uni}
        for name, sel in groups.items():
            subsets[mode][name] = uni.merge(sel[key], on=key, how="inner")
    names = ["Universum", *groups]
    for name in names:
        sk = summarize(subsets["stop"][name], p)
        so = summarize(subsets["ohlc"][name], p)
        if not sk.get("n_trades"):
            lines.append(f"| {name} | 0 | – | – | – | – | – | – | – |")
            continue
        lines.append(
            f"| {name} | {sk['n_trades']} | {fmt_pct(sk['hit_rate'], 1)} | "
            f"{fmt_pct(so['hit_rate'], 1)} | {fmt_pct(sk['avg_net_return'], 3)} | "
            f"{fmt_pct(so['avg_net_return'], 3)} | {fmt_pct(sk['ambiguous_share'], 1)} | "
            f"{fmt_pct(sk['time_stop_share'], 1)} | {sk['avg_days_held']:.1f} |"
        )

    lines += ["", "Differenz Ø Netto/Trade gegenüber Universum (Datumsblock-Bootstrap, 95 %-KI):", ""]
    for name in groups:
        parts = []
        for mode, label in MODES.items():
            tr = subsets[mode][name]
            if tr.empty:
                continue
            diff, lo, hi = block_bootstrap_diff(
                per_date_net(tr), per_date_net(subsets[mode]["Universum"]), block=block
            )
            sig = "✅" if lo > 0 else ("❌" if hi < 0 else "n.s.")
            parts.append(f"{label} {fmt_pct(diff, 3)} [{fmt_pct(lo, 3)} … {fmt_pct(hi, 3)}] {sig}")
        lines.append(f"- {name}: " + " · ".join(parts))

    # per year (OHLC path): universe vs every group
    ohlc = subsets["ohlc"]
    lines += ["", "Pro Jahr (OHLC-Pfad, Ø Netto/Trade):", "",
              "| Jahr | " + " | ".join(names) + " |",
              "|---|" + "---|" * len(names)]
    for year in sorted(ohlc["Universum"]["snapshot_date"].dt.year.unique()):
        cells = []
        for name in names:
            tr = ohlc[name][ohlc[name]["snapshot_date"].dt.year == year]
            cells.append(fmt_pct(summarize(tr, p).get("avg_net_return"), 3))
        lines.append(f"| {year} | " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging("INFO")

    from trading_signals.analysis.barrier import BarrierParams, simulate_signals
    from trading_signals.db.session import get_session
    from trading_signals.utils.retention import ml_start_date

    start = args.start or ml_start_date()
    with get_session() as session:
        features = load_features(session, start, args.end)
        prices = load_prices(session, start - timedelta(days=400))
    logger.info(f"{len(features)} snapshots ({features['snapshot_date'].nunique()} dates), "
                f"{len(prices)} price rows")
    if features.empty:
        logger.error("No feature snapshots – abort")
        return 1

    scored = tradable(score_all(features))
    logger.info(f"{len(scored)} scorable snapshots")
    good_regime = regime_dates(features, prices)
    top = top_n(scored, args.top_n)
    bottom = top_n(scored, args.top_n, ascending=True)
    top_f = top_n(filtered(scored, args.max_days), args.top_n)
    top_fr = top_f[top_f["snapshot_date"].isin(good_regime)]
    n_dates = scored["snapshot_date"].nunique()
    logger.info(f"Favourable regime on {len(good_regime & set(scored['snapshot_date']))}"
                f"/{n_dates} dates")

    groups = {
        f"Top {args.top_n}": top,
        f"Bottom {args.top_n}": bottom,
        f"Top {args.top_n} gefiltert": top_f,
        f"Top {args.top_n} gefiltert + Regime": top_fr,
    }
    px = prices[["ticker", "trade_date", "open", "high", "low", "close"]]
    signals = scored[["snapshot_date", "ticker"]]

    lines = [
        "# Barrier-Backtest – aktueller Context-Pack-Score",
        "",
        f"Erstellt: {datetime.now():%Y-%m-%d %H:%M} · Signale {scored['snapshot_date'].min():%Y-%m-%d} "
        f"– {scored['snapshot_date'].max():%Y-%m-%d} · {n_dates} Tage · Kosten {fmt_pct(args.cost, 3)} "
        f"Round-Trip",
        "",
        "Einstieg Open d+1 · Gap → Ausführung zum Open · gewichtet je Signaltag gleich.",
        "Tag mit Ziel **und** Stop berührt (mehrdeutig): *konservativ* = Stop zählt (Untergrenze); "
        "*OHLC-Pfad* = grüne Kerze O→L→H→C (Stop zuerst), rote Kerze O→H→L→C (Ziel zuerst).",
        f"Filter: ATR {fmt_pct(FILTER_ATR_MIN, 0)}–{fmt_pct(FILTER_ATR_MAX, 0)}, keine Earnings in der "
        f"Haltedauer · Regime: SPY > SMA{SMA_REGIME} und VIX < {REGIME_VIX_MAX:.0f}.",
        "",
    ]
    for tp in parse_floats(args.tp):
        for mult in parse_floats(args.sl_mult):
            universe = {}
            for mode in MODES:
                p = BarrierParams(tp=tp, sl=tp * mult, max_days=args.max_days,
                                  cost=args.cost, ambiguous=mode)
                logger.info(f"Simulating {p.label} ({mode}) …")
                universe[mode] = simulate_signals(signals, px, p)
            lines += run_combo(p, universe, groups, args.block)

    report = "\n".join(lines)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"barrier_backtest_{date.today():%Y-%m-%d}.md"
    out_file.write_text(report, encoding="utf-8")
    print(report)
    logger.info(f"Report written to {out_file.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
