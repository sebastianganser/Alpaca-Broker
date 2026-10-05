"""Pure statistics helpers for cross-sectional feature analysis.

All functions are side-effect free (no DB / IO) and unit-tested in
``tests/unit/test_analysis_stats.py``.

Why these helpers exist (review finding H4):

* Pooled Spearman over all rows mixes time-series and cross-sectional
  variation and treats ~700 rows per day as independent. The standard
  for signal research is the **daily rank IC** (Spearman per date), whose
  mean is tested against zero.
* Forward-return targets with horizon ``h`` overlap for consecutive
  dates, so daily ICs are autocorrelated up to lag ``h-1``. t-stats
  therefore use a **Newey-West (HAC)** standard error.
* Bootstraps must resample **contiguous blocks of dates**, not rows.
* Model validation must be **purged** (drop the last ``h`` training
  dates before each test block) to avoid label overlap leakage.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as _sps

from trading_signals.db.models.features import FEATURE_COLUMNS

DATE_COL = "snapshot_date"
_EPS = 1e-12


# ── Type helpers ─────────────────────────────────────────────────────


def to_float(series: pd.Series) -> pd.Series:
    """Convert a column to float64 (bool -> 0/1, Decimal -> float, None -> NaN).

    Non-convertible values become NaN.
    """
    s = series
    if s.dtype == bool or str(s.dtype) == "boolean":
        return s.astype("float64")
    if s.dtype == object:
        def _conv(v: Any) -> float:
            if v is None:
                return np.nan
            try:
                return float(v)
            except (TypeError, ValueError):
                return np.nan

        s = s.map(_conv)
    return pd.to_numeric(s, errors="coerce").astype("float64")


def coerce_numeric(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    """Return ``df[cols]`` converted to float64 (see :func:`to_float`)."""
    cols = list(cols)
    return pd.DataFrame({c: to_float(df[c]) for c in cols}, index=df.index)


def available_features(
    df: pd.DataFrame, features: Iterable[str] | None = None
) -> list[str]:
    """Features (default ``FEATURE_COLUMNS``) present in ``df`` and not all-NaN.

    Order follows ``features``. Bool columns count as numeric.
    """
    feats = FEATURE_COLUMNS if features is None else features
    out = []
    for f in feats:
        if f in df.columns and to_float(df[f]).notna().any():
            out.append(f)
    return out


def horizon_from_target(target: str) -> int:
    """Parse the horizon in trading days from a target name (``return_20d`` -> 20)."""
    m = re.search(r"(\d+)d$", target)
    if not m:
        raise ValueError(f"Cannot parse horizon from target {target!r}")
    return int(m.group(1))


def nw_lag_for_horizon(horizon: int) -> int:
    """Newey-West lag for overlapping ``horizon``-day targets: ``max(1, h-1)``."""
    return max(1, int(horizon) - 1)


# ── Newey-West inference ─────────────────────────────────────────────


def newey_west_se(series: Sequence[float] | pd.Series, lag: int) -> float:
    """HAC (Bartlett-kernel) standard error of the sample mean.

    Args:
        series: Ordered observations (NaNs are dropped).
        lag: Maximum autocovariance lag (``0`` -> iid SE with ddof=0).
    """
    x = np.asarray(series, dtype="float64")
    x = x[~np.isnan(x)]
    n = x.size
    if n < 2:
        return float("nan")
    e = x - x.mean()
    s = float(e @ e) / n
    for k in range(1, min(int(lag), n - 1) + 1):
        w = 1.0 - k / (lag + 1.0)
        s += 2.0 * w * float(e[k:] @ e[:-k]) / n
    if s <= 0:
        return float("nan")
    return math.sqrt(s / n)


def newey_west_tstat(series: Sequence[float] | pd.Series, lag: int) -> float:
    """HAC t-statistic of the mean (H0: mean == 0), Bartlett weights."""
    x = np.asarray(series, dtype="float64")
    x = x[~np.isnan(x)]
    se = newey_west_se(x, lag)
    if not np.isfinite(se) or se <= _EPS:
        return float("nan")
    return float(x.mean() / se)


def mean_test(series: Sequence[float] | pd.Series, lag: int) -> dict[str, float]:
    """Mean, std, NW t-stat and two-sided normal p-value of a daily series."""
    x = np.asarray(series, dtype="float64")
    x = x[~np.isnan(x)]
    n = int(x.size)
    if n == 0:
        nan = float("nan")
        return {"mean": nan, "std": nan, "t_nw": nan, "pvalue": nan, "n": 0}
    t = newey_west_tstat(x, lag)
    p = float(2.0 * _sps.norm.sf(abs(t))) if np.isfinite(t) else float("nan")
    std = float(x.std(ddof=1)) if n > 1 else float("nan")
    return {"mean": float(x.mean()), "std": std, "t_nw": t, "pvalue": p, "n": n}


def ic_summary(ic: Sequence[float] | pd.Series, horizon: int) -> dict[str, float]:
    """Summarise a daily IC series.

    Returns:
        ``mean_ic``, ``ic_std``, ``icir`` (mean/std, per-day), ``t_nw``
        (Newey-West, lag ``max(1, horizon-1)``), ``pvalue`` (two-sided,
        normal) and ``n_dates``.
    """
    r = mean_test(ic, nw_lag_for_horizon(horizon))
    std = r["std"]
    icir = r["mean"] / std if np.isfinite(std) and std > _EPS else float("nan")
    return {
        "mean_ic": r["mean"],
        "ic_std": std,
        "icir": float(icir),
        "t_nw": r["t_nw"],
        "pvalue": r["pvalue"],
        "n_dates": r["n"],
    }


# ── Cross-sectional statistics ───────────────────────────────────────


def daily_rank_ic(
    df: pd.DataFrame,
    feature: str,
    target: str,
    date_col: str = DATE_COL,
    min_obs: int = 10,
) -> pd.Series:
    """Spearman rank correlation between ``feature`` and ``target`` per date.

    Rows with NaN in feature or target are dropped (pairwise complete).
    Dates with fewer than ``min_obs`` observations or with a constant
    feature/target are skipped.

    Returns:
        Series indexed by date (sorted), named ``feature``.
    """
    sub = df[[date_col, feature, target]]
    frame = pd.DataFrame(
        {
            "d": sub[date_col].to_numpy(),
            "x": to_float(sub[feature]).to_numpy(),
            "y": to_float(sub[target]).to_numpy(),
        }
    ).dropna()
    if frame.empty:
        return pd.Series(dtype="float64", name=feature)
    g = frame.groupby("d", sort=True)
    ranks = g[["x", "y"]].rank(method="average")
    means = ranks.groupby(frame["d"]).transform("mean")
    dx = ranks["x"] - means["x"]
    dy = ranks["y"] - means["y"]
    agg = pd.DataFrame(
        {"d": frame["d"], "sxy": dx * dy, "sxx": dx * dx, "syy": dy * dy}
    ).groupby("d", sort=True).sum()
    n = g.size()
    valid = (n >= min_obs) & (agg["sxx"] > _EPS) & (agg["syy"] > _EPS)
    agg = agg[valid]
    ic = agg["sxy"] / np.sqrt(agg["sxx"] * agg["syy"])
    ic.name = feature
    ic.index.name = date_col
    return ic.astype("float64")


def ic_difference(
    df: pd.DataFrame,
    feature_a: str,
    feature_b: str,
    target: str,
    date_col: str = DATE_COL,
    min_obs: int = 10,
) -> tuple[pd.Series, pd.Series]:
    """Daily ICs of two features computed on their common rows.

    Returns:
        ``(ic_a, ic_b)`` aligned on the dates where both are defined.
    """
    sub = df[[date_col, feature_a, feature_b, target]].dropna()
    ic_a = daily_rank_ic(sub, feature_a, target, date_col, min_obs)
    ic_b = daily_rank_ic(sub, feature_b, target, date_col, min_obs)
    common = ic_a.index.intersection(ic_b.index)
    return ic_a.loc[common], ic_b.loc[common]


def cross_sectional_transform(
    df: pd.DataFrame,
    cols: Iterable[str],
    method: str = "rank",
    date_col: str = DATE_COL,
    centered: bool = False,
    dtype: str = "float64",
) -> pd.DataFrame:
    """Per-date cross-sectional transform of ``cols`` (NaN preserved).

    Args:
        method: ``"rank"`` -> uniform percentile rank in [0, 1]
            (``(rank-1)/(n-1)``, average ties; a single observation or a
            constant column maps to 0.5). ``"zscore"`` -> (x-mean)/std with
            ddof=0 (constant -> 0).
        centered: For ``"rank"``: subtract 0.5 (range [-0.5, 0.5]).

    Returns:
        Copy of ``df`` with ``cols`` replaced by the transformed values.
    """
    cols = list(cols)
    out = df.copy()
    if not cols:
        return out
    vals = coerce_numeric(out, cols)
    keys = out[date_col].to_numpy()
    g = vals.groupby(keys)
    if method == "rank":
        r = g.rank(method="average")
        n = g.transform("count")
        res = (r - 1.0) / (n - 1.0)
        res = res.where(n > 1, 0.5).where(vals.notna())
        if centered:
            res = res - 0.5
    elif method == "zscore":
        mean = g.transform("mean")
        std = g.transform("std", ddof=0)
        res = (vals - mean) / std
        res = res.where(std > _EPS, 0.0).where(vals.notna())
    else:
        raise ValueError(f"Unknown method {method!r} (use 'rank' or 'zscore')")
    for c in cols:
        out[c] = res[c].astype(dtype)
    return out


def excess_returns(
    df: pd.DataFrame,
    targets: Iterable[str],
    date_col: str = DATE_COL,
    benchmark: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Replace targets by excess returns.

    Default: target minus its per-date cross-sectional mean (over rows
    where the target is present). If ``benchmark`` maps a target to a
    column (e.g. ``{"return_20d": "spy_return_20d"}``) that column is
    subtracted instead.

    Returns:
        Copy of ``df`` with the targets replaced.
    """
    out = df.copy()
    for t in targets:
        if t not in out.columns:
            continue
        y = to_float(out[t])
        if benchmark and t in benchmark:
            out[t] = y - to_float(out[benchmark[t]])
        else:
            out[t] = y - y.groupby(out[date_col].to_numpy()).transform("mean")
    return out


def daily_group_difference(
    df: pd.DataFrame,
    group: pd.Series,
    target: str,
    date_col: str = DATE_COL,
    min_group: int = 3,
) -> pd.Series:
    """Per-date mean(target | group) - mean(target | not group).

    Args:
        group: Series aligned with ``df``; truthy = treatment, falsy =
            control, NaN = excluded.
        min_group: Dates need at least this many members in BOTH groups.

    Returns:
        Daily difference series indexed by date.
    """
    frame = pd.DataFrame(
        {
            "d": df[date_col].to_numpy(),
            "g": to_float(pd.Series(group, index=df.index)).to_numpy(),
            "y": to_float(df[target]).to_numpy(),
        }
    ).dropna()
    if frame.empty:
        return pd.Series(dtype="float64")
    frame["g"] = frame["g"] != 0
    agg = frame.groupby(["d", "g"])["y"].agg(["mean", "size"]).unstack("g")
    if (True not in agg["size"].columns) or (False not in agg["size"].columns):
        return pd.Series(dtype="float64")
    size_t = agg["size"][True].fillna(0)
    size_f = agg["size"][False].fillna(0)
    ok = (size_t >= min_group) & (size_f >= min_group)
    diff = (agg["mean"][True] - agg["mean"][False])[ok]
    diff.index.name = date_col
    diff.name = "diff"
    return diff.astype("float64")


def quintile_returns(
    df: pd.DataFrame,
    feature: str,
    target: str,
    date_col: str = DATE_COL,
    n_quantiles: int = 5,
    min_obs: int | None = None,
    horizon: int | None = None,
) -> dict[str, Any]:
    """Quantile portfolio returns with buckets assigned PER DATE.

    Buckets use average ranks (``ceil(rank/n * q)``), so tied values share
    a bucket (sparse/binary features may leave buckets empty). Mean target
    per (date, bucket), then averaged across dates. The long-short series
    is top minus bottom *non-empty* bucket per date, tested with a
    Newey-West t-stat (lag ``max(1, horizon-1)``; horizon parsed from the
    target name if not given).

    Returns:
        dict with ``quantile_means`` (Series 1..q), ``daily`` (dates x q),
        ``long_short`` (Series), ``long_short_mean``, ``long_short_t_nw``,
        ``long_short_pvalue``, ``n_dates``.
    """
    min_obs = min_obs or 2 * n_quantiles
    if horizon is None:
        try:
            horizon = horizon_from_target(target)
        except ValueError:
            horizon = 1
    frame = pd.DataFrame(
        {
            "d": df[date_col].to_numpy(),
            "x": to_float(df[feature]).to_numpy(),
            "y": to_float(df[target]).to_numpy(),
        }
    ).dropna()
    cols = list(range(1, n_quantiles + 1))
    empty = {
        "quantile_means": pd.Series(np.nan, index=cols),
        "daily": pd.DataFrame(columns=cols),
        "long_short": pd.Series(dtype="float64"),
        "long_short_mean": float("nan"),
        "long_short_t_nw": float("nan"),
        "long_short_pvalue": float("nan"),
        "n_dates": 0,
    }
    if frame.empty:
        return empty
    n = frame.groupby("d")["x"].transform("count")
    frame = frame[n >= min_obs]
    if frame.empty:
        return empty
    g = frame.groupby("d")["x"]
    r = g.rank(method="average")
    n = g.transform("count")
    q = np.ceil(r / n * n_quantiles).clip(1, n_quantiles).astype(int)
    daily = (
        frame.assign(q=q.to_numpy())
        .groupby(["d", "q"])["y"].mean()
        .unstack("q")
        .reindex(columns=cols)
        .sort_index()
    )
    daily.index.name = date_col
    top = daily.ffill(axis=1).iloc[:, -1]
    bottom = daily.bfill(axis=1).iloc[:, 0]
    ok = daily.notna().sum(axis=1) >= 2
    ls = (top - bottom)[ok]
    t = mean_test(ls, nw_lag_for_horizon(horizon))
    return {
        "quantile_means": daily.mean(axis=0),
        "daily": daily,
        "long_short": ls,
        "long_short_mean": t["mean"],
        "long_short_t_nw": t["t_nw"],
        "long_short_pvalue": t["pvalue"],
        "n_dates": int(daily.shape[0]),
    }


# ── Purged walk-forward CV ───────────────────────────────────────────


def purged_walk_forward_splits(
    dates: Sequence[Any] | pd.Series | np.ndarray,
    n_splits: int = 5,
    horizon: int = 20,
    embargo: int | None = None,
    min_train_dates: int = 1,
    expanding: bool = True,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Date-grouped CV splits with purge (and embargo) for overlapping labels.

    Unique dates are split into contiguous blocks; all rows of a date are
    always in the same fold.

    * ``expanding=True`` (walk-forward, default): ``n_splits + 1`` blocks,
      block ``k`` (k>=1) is the test block; training uses only dates
      strictly before the test block, minus the last ``horizon`` dates
      (purge – their labels overlap the test period).
    * ``expanding=False`` (purged k-fold): ``n_splits`` blocks; training
      uses all other dates except the ``horizon`` dates before the test
      block (purge) and the ``embargo`` dates after it (default
      ``horizon``).

    Splits with fewer than ``min_train_dates`` training dates are skipped.

    Returns:
        List of ``(train_idx, test_idx)`` positional row indices into
        ``dates`` (usable as ``cv=`` for scikit-learn).
    """
    d = pd.to_datetime(pd.Series(np.asarray(dates))).to_numpy()
    uniq = np.unique(d)
    m = uniq.size
    embargo = horizon if embargo is None else embargo
    if n_splits < 1 or m < n_splits + (1 if expanding else 0):
        return []
    pos = np.searchsorted(uniq, d)
    date_idx = np.arange(m)
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    n_blocks = n_splits + 1 if expanding else n_splits
    bounds = np.linspace(0, m, n_blocks + 1).astype(int)
    blocks = range(1, n_blocks) if expanding else range(n_blocks)
    for k in blocks:
        start, end = bounds[k], bounds[k + 1]  # test dates [start, end)
        if end <= start:
            continue
        test_mask = (date_idx >= start) & (date_idx < end)
        train_mask = date_idx < start - horizon
        if not expanding:
            train_mask |= date_idx >= end + embargo
        if train_mask.sum() < max(1, min_train_dates):
            continue
        train_idx = np.flatnonzero(train_mask[pos])
        test_idx = np.flatnonzero(test_mask[pos])
        if test_idx.size == 0 or train_idx.size == 0:
            continue
        splits.append((train_idx, test_idx))
    return splits


# ── Date-block bootstrap ─────────────────────────────────────────────


def block_bootstrap_indices(
    n: int, block_len: int, n_boot: int, seed: int | None = None
) -> Iterator[np.ndarray]:
    """Yield ``n_boot`` moving-block-bootstrap index arrays of length ``n``."""
    if n <= 0:
        return
    rng = np.random.default_rng(seed)
    block_len = int(max(1, min(block_len, n)))
    n_blocks = math.ceil(n / block_len)
    offsets = np.arange(block_len)
    for _ in range(n_boot):
        starts = rng.integers(0, n - block_len + 1, size=n_blocks)
        yield (starts[:, None] + offsets[None, :]).ravel()[:n]


def date_block_bootstrap(
    data: pd.Series | np.ndarray | Sequence[float] | pd.DataFrame,
    block_len: int,
    n_boot: int = 1000,
    seed: int | None = None,
    statistic: Callable[[Any], float] | None = None,
    date_col: str = DATE_COL,
) -> np.ndarray:
    """Moving block bootstrap over dates.

    Args:
        data: Either an ordered daily series (e.g. daily ICs) – blocks of
            consecutive observations are resampled and ``statistic``
            (default ``np.nanmean``) is applied to the resampled values –
            or a row-level DataFrame, in which case blocks of consecutive
            unique dates are resampled, all rows of the chosen dates are
            concatenated and ``statistic(df_resampled)`` is applied
            (``statistic`` required).
        block_len: Block length in dates (use >= horizon for overlapping
            targets).

    Returns:
        Array of ``n_boot`` bootstrap statistics.
    """
    if isinstance(data, pd.DataFrame):
        if statistic is None:
            raise ValueError("statistic is required for DataFrame input")
        codes, uniq = pd.factorize(data[date_col], sort=True)
        order = np.argsort(codes, kind="stable")
        counts = np.bincount(codes, minlength=len(uniq))
        offsets = np.concatenate([[0], np.cumsum(counts)])
        out = []
        for idx in block_bootstrap_indices(len(uniq), block_len, n_boot, seed):
            rows = np.concatenate([order[offsets[i]:offsets[i + 1]] for i in idx])
            out.append(statistic(data.iloc[rows]))
        return np.asarray(out, dtype="float64")
    x = np.asarray(data, dtype="float64")
    x = x[~np.isnan(x)]
    stat = statistic or np.nanmean
    gen = block_bootstrap_indices(x.size, block_len, n_boot, seed)
    return np.asarray([stat(x[idx]) for idx in gen], dtype="float64")


def bootstrap_ci(samples: np.ndarray, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile confidence interval of bootstrap samples."""
    s = np.asarray(samples, dtype="float64")
    s = s[~np.isnan(s)]
    if s.size == 0:
        return float("nan"), float("nan")
    return (
        float(np.percentile(s, 100 * alpha / 2)),
        float(np.percentile(s, 100 * (1 - alpha / 2))),
    )
