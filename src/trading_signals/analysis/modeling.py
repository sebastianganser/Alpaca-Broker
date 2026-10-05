"""Leakage-safe model-based feature importance (RF + LASSO).

Design (review finding H4):

* Features are cross-sectionally ranked **per date** (uniform [0, 1]).
  This transform is fit-free (uses only same-day data), so it cannot leak
  across folds, and it removes scale/outlier issues.
* Target is an **excess** return (minus per-date cross-sectional mean).
* Validation uses :func:`~trading_signals.analysis.stats.purged_walk_forward_splits`
  grouped by date with a purge of ``horizon`` dates before each test block.
* Imputation (median) and scaling are scikit-learn ``Pipeline`` steps and
  are therefore re-fit **inside every fold**.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Lasso, lasso_path
from sklearn.metrics import r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from trading_signals.analysis.stats import (
    DATE_COL,
    cross_sectional_transform,
    daily_rank_ic,
    purged_walk_forward_splits,
)
from trading_signals.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_RF_PARAMS: dict[str, Any] = {
    "n_estimators": 200,
    "max_depth": 8,
    "min_samples_leaf": 50,
    "max_features": 0.33,
    "max_samples": 0.5,
    "n_jobs": -1,
    "random_state": 42,
}


def preprocessing_steps() -> list[tuple[str, Any]]:
    """Fresh (unfitted) imputer + scaler steps for a Pipeline."""
    return [
        ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scale", StandardScaler()),
    ]


def prepare_model_frame(
    df: pd.DataFrame,
    features: list[str],
    target: str,
    date_col: str = DATE_COL,
    max_rows: int | None = 300_000,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.Series, np.ndarray]:
    """Rank features per date, drop rows without target, optionally subsample.

    Features without any cross-sectional variation (e.g. market-wide
    macro/breadth columns -> every ranked value is 0.5) are dropped.

    Returns:
        ``(X, y, dates)`` sorted by date; ``X`` columns are the kept
        features (float32 ranks, NaN = missing).
    """
    sub = df.loc[df[target].notna(), [date_col, target, *features]]
    if sub.empty:
        return pd.DataFrame(columns=features), pd.Series(dtype="float64"), np.array([])
    ranked = cross_sectional_transform(sub, features, "rank", date_col, dtype="float32")
    keep = [
        f for f in features
        if ranked[f].notna().any() and (ranked[f].dropna() != 0.5).any()
    ]
    ranked = ranked[[date_col, target, *keep]]
    if max_rows and len(ranked) > max_rows:
        ranked = ranked.sample(n=max_rows, random_state=seed)
    ranked = ranked.sort_values(date_col, kind="stable").reset_index(drop=True)
    x = ranked[keep]
    y = ranked[target].astype("float64")
    dates = pd.to_datetime(ranked[date_col]).to_numpy()
    return x, y, dates


def _mean_daily_ic(dates: np.ndarray, pred: np.ndarray, y: np.ndarray) -> float:
    frame = pd.DataFrame({DATE_COL: dates, "pred": pred, "y": y})
    ic = daily_rank_ic(frame, "pred", "y", min_obs=10)
    return float(ic.mean()) if not ic.empty else float("nan")


def walk_forward_rf(
    x: pd.DataFrame,
    y: pd.Series,
    dates: np.ndarray,
    horizon: int,
    n_splits: int = 4,
    min_train_dates: int = 20,
    rf_params: dict[str, Any] | None = None,
    n_repeats: int = 5,
    perm_max_rows: int = 50_000,
    seed: int = 42,
) -> dict[str, Any]:
    """Random forest in purged walk-forward folds + permutation importance.

    Permutation importance (R² drop) is computed on the LAST out-of-sample
    fold with the model trained on that fold's training data.

    Returns:
        ``{"importance": {feature: {"importance", "std"}}, "folds": [...]}``
        or ``{}`` if no valid split exists.
    """
    splits = purged_walk_forward_splits(
        dates, n_splits=n_splits, horizon=horizon, min_train_dates=min_train_dates
    )
    if not splits:
        return {}
    params = {**DEFAULT_RF_PARAMS, **(rf_params or {})}
    folds = []
    pipe = None
    for i, (tr, te) in enumerate(splits):
        pipe = Pipeline(
            preprocessing_steps() + [("rf", RandomForestRegressor(**params))]
        )
        pipe.fit(x.iloc[tr], y.iloc[tr])
        pred = pipe.predict(x.iloc[te])
        fold = {
            "fold": i,
            "n_train": int(tr.size),
            "n_test": int(te.size),
            "r2": float(r2_score(y.iloc[te], pred)),
            "mean_ic": _mean_daily_ic(dates[te], pred, y.iloc[te].to_numpy()),
        }
        folds.append(fold)
        logger.info(
            f"RF fold {i}: train={fold['n_train']} test={fold['n_test']} "
            f"R2={fold['r2']:.4f} OOS mean IC={fold['mean_ic']:.4f}"
        )
    _, te = splits[-1]
    max_samples = min(1.0, perm_max_rows / max(te.size, 1))
    perm = permutation_importance(
        pipe, x.iloc[te], y.iloc[te], n_repeats=n_repeats, random_state=seed,
        n_jobs=1, max_samples=max_samples,
    )
    importance = {
        f: {
            "importance": float(perm.importances_mean[j]),
            "std": float(perm.importances_std[j]),
        }
        for j, f in enumerate(x.columns)
    }
    return {"importance": importance, "folds": folds}


def purged_lasso(
    x: pd.DataFrame,
    y: pd.Series,
    dates: np.ndarray,
    horizon: int,
    n_splits: int = 4,
    min_train_dates: int = 20,
    n_alphas: int = 30,
    eps: float = 1e-3,
) -> dict[str, Any]:
    """LASSO with alpha chosen by purged walk-forward CV.

    Equivalent to ``LassoCV(cv=purged_splits)`` but the imputer and scaler
    are re-fit inside each fold (``LassoCV`` inside a Pipeline would fit
    them once on all rows before its inner CV). The final model is
    ``Pipeline(imputer, scaler, Lasso(best_alpha))`` fit on all rows.

    Returns:
        ``{"coef": {feature: coef}, "alpha": float, "cv_mse": [...],
        "folds": [...]}`` or ``{}`` if no valid split exists.
    """
    splits = purged_walk_forward_splits(
        dates, n_splits=n_splits, horizon=horizon, min_train_dates=min_train_dates
    )
    if not splits:
        return {}
    y_arr = y.to_numpy(dtype="float64")

    # Alpha grid from the first training fold (no test data involved).
    tr0, _ = splits[0]
    pre0 = Pipeline(preprocessing_steps()).fit(x.iloc[tr0])
    xt0 = np.asarray(pre0.transform(x.iloc[tr0]), dtype="float64")
    yt0 = y_arr[tr0] - y_arr[tr0].mean()
    alpha_max = float(np.max(np.abs(xt0.T @ yt0)) / max(len(yt0), 1)) or 1e-6
    alphas = np.logspace(np.log10(alpha_max), np.log10(alpha_max * eps), n_alphas)

    mse = np.full((len(splits), n_alphas), np.nan)
    folds = []
    for i, (tr, te) in enumerate(splits):
        pre = Pipeline(preprocessing_steps()).fit(x.iloc[tr])
        xtr = np.asarray(pre.transform(x.iloc[tr]), dtype="float64")
        xte = np.asarray(pre.transform(x.iloc[te]), dtype="float64")
        ym = y_arr[tr].mean()
        _, coefs, _ = lasso_path(xtr, y_arr[tr] - ym, alphas=alphas, max_iter=5000)
        pred = xte @ coefs + ym  # (n_test, n_alphas)
        mse[i] = ((pred - y_arr[te][:, None]) ** 2).mean(axis=0)
        best_i = int(np.nanargmin(mse[i]))
        folds.append(
            {
                "fold": i,
                "n_train": int(tr.size),
                "n_test": int(te.size),
                "mean_ic_best_alpha": _mean_daily_ic(
                    dates[te], pred[:, best_i], y_arr[te]
                ),
            }
        )
    cv_mse = np.nanmean(mse, axis=0)
    best_alpha = float(alphas[int(np.nanargmin(cv_mse))])
    final = Pipeline(
        preprocessing_steps() + [("lasso", Lasso(alpha=best_alpha, max_iter=10000))]
    )
    final.fit(x, y_arr)
    coef = final.named_steps["lasso"].coef_
    logger.info(f"LASSO purged-CV alpha: {best_alpha:.6g}")
    return {
        "coef": {f: float(coef[j]) for j, f in enumerate(x.columns)},
        "alpha": best_alpha,
        "cv_mse": [float(v) for v in cv_mse],
        "folds": folds,
    }
