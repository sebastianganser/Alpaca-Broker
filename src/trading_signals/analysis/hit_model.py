"""Walk-forward hit-probability model for the barrier trade (concept §7.2, step B).

Question: do the combined features, used out of sample, pick trades
(+1 % take profit / −2 % stop / 14 sessions) that make money after costs?

Pure building blocks (unit-tested); the DB loading and the report live in
``scripts/analysis/walkforward_hit_model.py``.

* :func:`build_design` – ticker features ranked per date, market-wide
  features and ``atr_14_pct`` additionally raw; label/returns.
* :func:`quarter_splits` – expanding walk-forward, one test quarter per
  fold, purge of ``PURGE_SESSIONS`` dates before each test quarter.
* :func:`walk_forward_predict` – fixed-parameter models (no tuning) fit per
  fold, out-of-sample P(hit) for every test row.
* :func:`select_candidates` – per date the top ``k`` by P(hit) with
  P ≥ threshold, otherwise no candidate.
* :func:`evaluate_selection` / :func:`choose_rule` – date-weighted trade
  statistics vs. the universe, rule choice on the development period.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from trading_signals.analysis.barrier import MAIN_BARRIER, block_bootstrap_diff
from trading_signals.analysis.feature_groups import is_market_wide
from trading_signals.analysis.stats import cross_sectional_transform
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

DATE_COL = "snapshot_date"
#: Label window = entry d+1 plus 14 sessions → purge 16 dates before a test block.
PURGE_SESSIONS = MAIN_BARRIER.max_days + 2
#: Hit rate at which the average net trade is zero (TP/SL/costs of MAIN_BARRIER).
BREAK_EVEN_HIT_RATE = (MAIN_BARRIER.sl + MAIN_BARRIER.cost) / (MAIN_BARRIER.tp + MAIN_BARRIER.sl)
#: Net return of a stopped trade (used for the conservative variant).
STOP_NET = -MAIN_BARRIER.sl - MAIN_BARRIER.cost
#: Features that are also fed in raw (absolute level matters for fixed barriers).
RAW_EXTRA = ("atr_14_pct",)

MODEL_NAMES = ("logit", "gbm")


@dataclass(frozen=True)
class Rule:
    """Daily selection rule: top ``k`` by ``model`` score with P ≥ ``threshold``."""

    model: str
    k: int
    threshold: float

    @property
    def label(self) -> str:
        thr = "ohne Schwelle" if self.threshold <= 0 else f"P ≥ {self.threshold:.3f}"
        return f"{self.model}, max. {self.k}/Tag, {thr}"


# ── Design matrix ────────────────────────────────────────────────────


def build_design(
    df: pd.DataFrame, features: Sequence[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Model inputs + per-row metadata.

    Args:
        df: Snapshot rows with ``snapshot_date``, ``ticker``, ``features``,
            ``barrier_outcome``, ``barrier_ambiguous``, ``return_barrier_14d``.
            Rows without label are dropped.
        features: Candidate feature columns (missing ones are ignored).

    Returns:
        ``(X, meta)`` sorted by date with aligned positional index. ``X``:
        ticker features as per-date ranks (NaN kept), market-wide features
        and :data:`RAW_EXTRA` raw (suffix ``__raw``), float32. ``meta``:
        ``snapshot_date``, ``ticker``, ``y`` (1 = take profit), ``net``,
        ``net_cons`` (ambiguous take-profit days counted as stop).
    """
    feats = [f for f in features if f in df.columns]
    sub = df[df["barrier_outcome"].notna() & df["return_barrier_14d"].notna()].copy()
    sub[DATE_COL] = pd.to_datetime(sub[DATE_COL])
    sub = sub.sort_values([DATE_COL, "ticker"], kind="stable").reset_index(drop=True)

    market = [f for f in feats if is_market_wide(f)]
    ticker_feats = [f for f in feats if not is_market_wide(f)]
    ranked = cross_sectional_transform(sub, ticker_feats, "rank", DATE_COL, dtype="float32")
    x = ranked[ticker_feats].copy()
    for f in [*market, *[r for r in RAW_EXTRA if r in feats]]:
        x[f"{f}__raw"] = pd.to_numeric(sub[f], errors="coerce").astype("float32")
    # drop inputs without any information
    keep = [c for c in x.columns if x[c].notna().any() and x[c].nunique(dropna=True) > 1]
    x = x[keep].astype("float32")

    outcome = pd.to_numeric(sub["barrier_outcome"], errors="coerce")
    net = pd.to_numeric(sub["return_barrier_14d"], errors="coerce").astype(float)
    amb = sub["barrier_ambiguous"].fillna(False).astype(bool)
    meta = pd.DataFrame({
        DATE_COL: sub[DATE_COL],
        "ticker": sub["ticker"],
        "y": (outcome == 1).astype(int),
        "net": net,
        "net_cons": np.where(amb & (outcome == 1), STOP_NET, net),
    })
    return x, meta


# ── Walk-forward splits ──────────────────────────────────────────────


def quarter_splits(
    dates: pd.Series,
    first_test: pd.Timestamp | str,
    purge: int = PURGE_SESSIONS,
    min_train_dates: int = 120,
) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """Expanding walk-forward folds, one calendar quarter per test block.

    Training uses all dates strictly before the test quarter minus the last
    ``purge`` dates (their labels reach into the test period).

    Returns:
        ``[(quarter_label, train_idx, test_idx), ...]`` (positional indices).
    """
    d_np = pd.to_datetime(pd.Series(np.asarray(dates))).to_numpy(dtype="datetime64[ns]")
    uniq = np.unique(d_np)
    quarters = pd.DatetimeIndex(uniq).to_period("Q")
    first_q = pd.Period(pd.Timestamp(first_test), freq="Q")
    out = []
    for q in sorted(set(quarters[quarters >= first_q])):
        q_dates = uniq[quarters == q]
        start_pos = int(np.searchsorted(uniq, q_dates[0]))
        train_end = start_pos - purge
        if train_end < min_train_dates:
            continue
        train_dates = uniq[:train_end]
        tr = np.flatnonzero(np.isin(d_np, train_dates))
        te = np.flatnonzero(np.isin(d_np, q_dates))
        out.append((str(q), tr, te))
    return out


def mean_daily_auc(dates: np.ndarray, y: np.ndarray, p: np.ndarray) -> float:
    """Mean of per-date AUCs (pure cross-sectional ranking skill, 0.5 = chance).

    Dates with only one class are skipped.
    """
    frame = pd.DataFrame({"d": dates, "y": y, "p": p})
    aucs = [
        roc_auc_score(g["y"], g["p"])
        for _, g in frame.groupby("d", sort=False)
        if 0 < g["y"].sum() < len(g)
    ]
    return float(np.mean(aucs)) if aucs else float("nan")


# ── Models ───────────────────────────────────────────────────────────


def make_model(name: str, seed: int = 42) -> Pipeline:
    """Fixed-parameter pipelines (deliberately not tuned, concept §7.2)."""
    if name == "logit":
        return Pipeline([
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(C=0.1, max_iter=1000)),
        ])
    if name == "gbm":
        return Pipeline([
            ("clf", HistGradientBoostingClassifier(
                max_depth=3, learning_rate=0.05, max_iter=150,
                min_samples_leaf=500, l2_regularization=1.0,
                early_stopping=False, random_state=seed,
            )),
        ])
    raise ValueError(f"unknown model {name!r}")


def walk_forward_predict(
    x: pd.DataFrame,
    meta: pd.DataFrame,
    splits: Iterable[tuple[str, np.ndarray, np.ndarray]],
    models: Sequence[str] = MODEL_NAMES,
    max_train_rows: int | None = 400_000,
    seed: int = 42,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Out-of-sample P(hit) per test row and model.

    Returns:
        ``(preds, folds)``: ``preds`` = ``meta`` rows of all test blocks plus
        ``quarter`` and one ``p_<model>`` column each; ``folds`` = per fold
        and model ``{quarter, model, n_train, n_test, auc, base_rate}``.
    """
    rng = np.random.default_rng(seed)
    parts, folds = [], []
    for quarter, tr, te in splits:
        if max_train_rows and tr.size > max_train_rows:
            tr = np.sort(rng.choice(tr, size=max_train_rows, replace=False))
        y_tr = meta["y"].to_numpy()[tr]
        x_tr = x.iloc[tr]
        # inputs without variation in THIS training window (e.g. sources that
        # start later) carry no information and break HGB binning
        cols = [c for c in x.columns if x_tr[c].nunique(dropna=True) > 1]
        x_tr, x_te = x_tr[cols], x.iloc[te][cols]
        part = meta.iloc[te].copy()
        part["quarter"] = quarter
        for name in models:
            model = make_model(name, seed)
            model.fit(x_tr, y_tr)
            p = model.predict_proba(x_te)[:, 1]
            part[f"p_{name}"] = p
            y_te = part["y"].to_numpy()
            auc = float(roc_auc_score(y_te, p)) if 0 < y_te.sum() < y_te.size else float("nan")
            daily = mean_daily_auc(part[DATE_COL].to_numpy(), y_te, p)
            folds.append({
                "quarter": quarter, "model": name, "n_train": int(tr.size),
                "n_test": int(te.size), "auc": auc, "daily_auc": daily,
                "base_rate": float(y_te.mean()), "mean_p": float(p.mean()),
            })
            logger.info(f"{quarter} {name}: train={tr.size} test={te.size} AUC={auc:.4f} "
                        f"daily AUC={daily:.4f} hit={y_te.mean():.3f} mean P={p.mean():.3f}")
        parts.append(part)
    preds = pd.concat(parts, ignore_index=True) if parts else meta.iloc[:0].copy()
    return preds, folds


# ── Selection + evaluation ───────────────────────────────────────────


def select_candidates(preds: pd.DataFrame, rule: Rule) -> pd.DataFrame:
    """Per date the top ``rule.k`` rows by P(hit) with P ≥ threshold."""
    col = f"p_{rule.model}"
    eligible = preds[preds[col] >= rule.threshold]
    return (
        eligible.sort_values([DATE_COL, col], ascending=[True, False], kind="stable")
        .groupby(DATE_COL, sort=False)
        .head(rule.k)
    )


def evaluate_selection(
    selected: pd.DataFrame,
    universe: pd.DataFrame,
    block: int = 20,
    bootstrap: bool = True,
) -> dict[str, Any]:
    """Date-weighted statistics of the selected trades vs. the universe.

    The universe comparison uses only dates with at least one candidate.
    """
    n_days = int(universe[DATE_COL].nunique())
    out: dict[str, Any] = {"n_days": n_days, "n_trades": int(len(selected))}
    if selected.empty:
        out.update(days_with=0, no_candidate_share=1.0)
        return out
    per_sel = selected.groupby(DATE_COL).agg(
        net=("net", "mean"), cons=("net_cons", "mean"), hit=("y", "mean")
    )
    per_uni = universe.groupby(DATE_COL).agg(net=("net", "mean"), cons=("net_cons", "mean"),
                                             hit=("y", "mean"))
    per_uni = per_uni.loc[per_uni.index.isin(per_sel.index)]
    out.update(
        days_with=int(len(per_sel)),
        no_candidate_share=1.0 - len(per_sel) / n_days if n_days else float("nan"),
        hit_rate=float(per_sel["hit"].mean()),
        net=float(per_sel["net"].mean()),
        net_cons=float(per_sel["cons"].mean()),
        uni_hit_rate=float(per_uni["hit"].mean()),
        uni_net=float(per_uni["net"].mean()),
        uni_net_cons=float(per_uni["cons"].mean()),
    )
    if bootstrap:
        diff, lo, hi = block_bootstrap_diff(per_sel["net"], per_uni["net"], block=block)
        dc, lc, hc = block_bootstrap_diff(per_sel["cons"], per_uni["cons"], block=block)
        out.update(diff=diff, diff_lo=lo, diff_hi=hi,
                   diff_cons=dc, diff_cons_lo=lc, diff_cons_hi=hc)
    return out


def choose_rule(
    dev_preds: pd.DataFrame,
    models: Sequence[str],
    ks: Sequence[int],
    thresholds: Sequence[float],
    min_days: int = 60,
) -> tuple[Rule | None, pd.DataFrame]:
    """Pick the rule with the best date-weighted net return on the dev period.

    Rules with fewer than ``min_days`` candidate days are not eligible.

    Returns:
        ``(best_rule or None, table of all rules)``.
    """
    rows = []
    for m in models:
        for k in ks:
            for t in thresholds:
                rule = Rule(m, k, t)
                ev = evaluate_selection(select_candidates(dev_preds, rule), dev_preds,
                                        bootstrap=False)
                rows.append({"rule": rule, **ev})
    table = pd.DataFrame(rows)
    ok = table[table["days_with"] >= min_days]
    if ok.empty or "net" not in ok:
        return None, table
    best = ok.sort_values("net", ascending=False).iloc[0]["rule"]
    return best, table


def calibration_table(preds: pd.DataFrame, model: str, bins: int = 10) -> pd.DataFrame:
    """Predicted vs. realised hit rate per P(hit) quantile bin."""
    col = f"p_{model}"
    q = pd.qcut(preds[col], q=bins, duplicates="drop")
    return (
        preds.groupby(q, observed=True)
        .agg(n=("y", "size"), mean_p=(col, "mean"), hit_rate=("y", "mean"),
             net=("net", "mean"))
        .reset_index(drop=True)
    )


__all__ = [
    "BREAK_EVEN_HIT_RATE",
    "MODEL_NAMES",
    "PURGE_SESSIONS",
    "Rule",
    "build_design",
    "calibration_table",
    "choose_rule",
    "evaluate_selection",
    "make_model",
    "mean_daily_auc",
    "quarter_splits",
    "select_candidates",
    "walk_forward_predict",
]
