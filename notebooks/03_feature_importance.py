# %% [markdown]
# # Sprint 9 (rebuilt): Feature Importance & Hypothesis Testing (Exploratory Analysis)
#
# This notebook conducts a leakage-safe feature importance analysis on the calculated
# features and tests specific hypotheses regarding trading signals.
#
# **Why rebuilt?** The previous version fit the imputer/scaler on the full data, used
# `TimeSeriesSplit` without purge/embargo (20-day targets overlap!), row-iid Welch
# t-tests
# and row-iid bootstraps, a hard-coded (~40 features short) feature list and contained a
# threshold bug (`analyst_rating_score > 3.5` although the score lies in [-1, 1]).
# Its results and the derived "Sprint 10 recommendations" were biased.
#
# ## Goals:
# 1. Evaluate feature importance using Random Forest (permutation, out-of-sample) and
# LASSO.
# 2. Compare rankings across methods and the mean daily rank IC.
# 3. Test specific hypotheses defined in `LEARNINGS_HYPOTHESES.md`.
# 4. Re-derive recommendations for the final scoring model (Sprint 10).
#
# ## Methodology
# - Window `snapshot_date >= ml_start_date()`, features = `FEATURE_COLUMNS` (model).
# - Target: **excess** `return_20d` (minus per-date cross-sectional mean).
# - Features are **ranked per date** (uniform [0, 1]) – fit-free, so no cross-fold
#   leakage.
# - **Purged walk-forward CV grouped by date**: training only before the test block, the
#   last `h` training dates before each test block are purged (label overlap). For the
#   purged k-fold variant an embargo of `h` dates after the test block is applied.
# - Median imputation and scaling are `Pipeline` steps -> re-fit **inside each fold**.
# - Hypotheses: per-date group differences / daily IC differences + Newey-West t;
#   **date-block bootstrap** CIs.

# %%
# Import libraries
import os
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from IPython.display import display
from sklearn.ensemble import RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import Pipeline

# Set plotting style
plt.style.use("seaborn-v0_8-whitegrid")
sns.set_context("notebook", font_scale=1.1)

# Add src directory to path for config
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath("__file__")), "..", "src")
)
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy import inspect as sa_inspect  # noqa: E402

from trading_signals.analysis.feature_report import (  # noqa: E402
    build_load_query,
    prepare_frame,
)
from trading_signals.analysis.modeling import (  # noqa: E402
    DEFAULT_RF_PARAMS,
    prepare_model_frame,
    preprocessing_steps,
    purged_lasso,
    walk_forward_rf,
)
from trading_signals.analysis.stats import (  # noqa: E402
    available_features,
    bootstrap_ci,
    daily_group_difference,
    daily_rank_ic,
    date_block_bootstrap,
    ic_difference,
    ic_summary,
    mean_test,
    nw_lag_for_horizon,
    purged_walk_forward_splits,
)
from trading_signals.config import get_settings  # noqa: E402
from trading_signals.utils.retention import ml_start_date  # noqa: E402

# %% [markdown]
# ## 1. Daten laden und aufbereiten (Data Loading & Preparation)
#
# - Explicit columns from `signals.feature_snapshots`, `snapshot_date >=
#   ml_start_date()`
# - Features from `FEATURE_COLUMNS`; bool -> float; targets -> excess returns
# - Features ranked per date, rows without target dropped (optional row cap for speed)
# - Purged walk-forward splits grouped by date

# %%
# Load data from database
print("Loading data from database...")
settings = get_settings()
engine = create_engine(settings.database_url)
start = ml_start_date()
table_cols = [
    c["name"]
    for c in sa_inspect(engine).get_columns("feature_snapshots", schema="signals")
]
sql, params = build_load_query(table_cols, start)
df = prepare_frame(pd.read_sql(sql, engine, params=params))

print(f"Total rows loaded: {len(df)} (from {start})")
d_min, d_max = df["snapshot_date"].min().date(), df["snapshot_date"].max().date()
print(f"Date range: {d_min} to {d_max}")

all_features = available_features(df)
print(f"Features available (from FEATURE_COLUMNS): {len(all_features)}")

target_col = "return_20d"
HORIZON = 20
N_SPLITS = 4
MAX_ROWS = 400_000  # uniform row subsample (all dates kept); None = all rows

X, y, dates = prepare_model_frame(df, all_features, target_col, max_rows=MAX_ROWS)
model_features = list(X.columns)
dropped = sorted(set(all_features) - set(model_features))
print(
    f"Model rows: {len(X)}, features with cross-sectional variation: "
    f"{len(model_features)}"
)
print(f"Dropped (no cross-sectional variation, e.g. market-wide): {dropped}")

splits = purged_walk_forward_splits(
    dates, n_splits=N_SPLITS, horizon=HORIZON, min_train_dates=60
)
for i, (tr, te) in enumerate(splits):
    print(
        f"Fold {i}: train {pd.Timestamp(dates[tr].min()).date()}.."
        f"{pd.Timestamp(dates[tr].max()).date()} ({len(tr)} rows) | "
        f"test {pd.Timestamp(dates[te].min()).date()}.."
        f"{pd.Timestamp(dates[te].max()).date()} ({len(te)} rows)"
    )

print("Data preparation complete.")

# %% [markdown]
# ## 2. Random Forest Feature Importance (Wichtigkeitsanalyse)
#
# RandomForestRegressor on excess `return_20d` inside each purged fold
# (`Pipeline(imputer, scaler, RF)`). We report out-of-sample R², MAE and the mean daily
# rank IC of the predictions per fold, MDI of the last fold (biased towards
# high-cardinality
# features – for reference only) and permutation importance on the last OOS fold.

# %%
print("Training Random Forest Regressor (purged walk-forward)...")
fold_rows = []
pipe = None
for i, (tr, te) in enumerate(splits):
    pipe = Pipeline(
        preprocessing_steps() + [("rf", RandomForestRegressor(**DEFAULT_RF_PARAMS))]
    )
    pipe.fit(X.iloc[tr], y.iloc[tr])
    y_pred = pipe.predict(X.iloc[te])
    pred_frame = pd.DataFrame(
        {"snapshot_date": dates[te], "pred": y_pred, "y": y.iloc[te].to_numpy()}
    )
    oos_ic = daily_rank_ic(pred_frame, "pred", "y")
    fold_rows.append(
        {
            "fold": i,
            "R2": r2_score(y.iloc[te], y_pred),
            "MAE": mean_absolute_error(y.iloc[te], y_pred),
            "OOS mean IC": oos_ic.mean(),
            "OOS IC t_NW": ic_summary(oos_ic, HORIZON)["t_nw"],
        }
    )
display(pd.DataFrame(fold_rows))

# a) MDI Importance (last fold model)
mdi_importances = pd.Series(
    pipe.named_steps["rf"].feature_importances_, index=model_features
).sort_values(ascending=False)

plt.figure(figsize=(12, 10))
sns.barplot(
    x=mdi_importances.head(20).values,
    y=mdi_importances.head(20).index,
    palette="viridis",
)
plt.title("Top 20 Features: Random Forest Impurity-based Importance (MDI, last fold)")
plt.xlabel("Gini Importance")
plt.tight_layout()
plt.show()

# b) Permutation Importance on the last out-of-sample fold
print("Calculating Permutation Importance (may take a minute)...")
_, te_last = splits[-1]
perm_result = permutation_importance(
    pipe,
    X.iloc[te_last],
    y.iloc[te_last],
    n_repeats=5,
    random_state=42,
    n_jobs=1,
    max_samples=min(1.0, 50_000 / len(te_last)),
)
perm_importances = pd.Series(
    perm_result.importances_mean, index=model_features
).sort_values(ascending=False)

plt.figure(figsize=(12, 10))
sns.barplot(
    x=perm_importances.head(20).values,
    y=perm_importances.head(20).index,
    palette="magma",
)
plt.title("Top 20 Features: RF Permutation Importance (last OOS fold)")
plt.xlabel("Mean R² decrease")
plt.tight_layout()
plt.show()

# %% [markdown]
# ### Evaluate for different horizons (return_1d, return_5d)
# Same purged walk-forward procedure with purge = horizon.

# %%
for tgt, h in [("return_1d", 1), ("return_5d", 5)]:
    if tgt not in df.columns:
        continue
    print(f"\nAnalyzing target: {tgt}")
    X_h, y_h, d_h = prepare_model_frame(df, all_features, tgt, max_rows=MAX_ROWS)
    res = walk_forward_rf(
        X_h,
        y_h,
        d_h,
        horizon=h,
        n_splits=N_SPLITS,
        min_train_dates=60,
        rf_params={"n_estimators": 100},
    )
    if not res:
        print("  not enough dates")
        continue
    print("  OOS mean IC per fold:", [round(f["mean_ic"], 4) for f in res["folds"]])
    imp = pd.Series({f: v["importance"] for f, v in res["importance"].items()})
    print(f"  Top 5 Features for {tgt} (permutation, last OOS fold):")
    print(imp.sort_values(ascending=False).head(5))

# %% [markdown]
# ## 3. LASSO Regression (LASSO-Modellierung & Selektion)
#
# LASSO shrinks less important coefficients to exactly zero. Alpha is chosen by purged
# walk-forward CV with imputer + scaler re-fit inside every fold; the final model is fit
# on all rows with the selected alpha.

# %%
print("Training LASSO with purged walk-forward CV...")
lasso_res = purged_lasso(
    X, y, dates, horizon=HORIZON, n_splits=N_SPLITS, min_train_dates=60
)
print(f"Optimal Alpha: {lasso_res['alpha']:.6g}")
display(pd.DataFrame(lasso_res["folds"]))

lasso_coefs = pd.Series(lasso_res["coef"])
non_zero_coefs = lasso_coefs[lasso_coefs.abs() > 1e-8].sort_values(
    key=abs, ascending=False
)
zero_features = lasso_coefs[lasso_coefs.abs() <= 1e-8].index.tolist()

print(
    f"\nLASSO retained {len(non_zero_coefs)} features and zeroed out "
    f"{len(zero_features)} features."
)

if not non_zero_coefs.empty:
    plt.figure(figsize=(10, 8))
    sns.barplot(
        x=non_zero_coefs.head(20).values,
        y=non_zero_coefs.head(20).index,
        palette="coolwarm",
    )
    plt.title("Top 20 Non-Zero LASSO Coefficients (per-date ranked features)")
    plt.xlabel("Coefficient Value")
    plt.tight_layout()
    plt.show()

# %% [markdown]
# ## 4. Method Comparison (Vergleich der Methoden)
#
# Compare Rankings: mean daily rank IC vs RF permutation (OOS) vs LASSO magnitude.

# %%
ic_stats = {}
for f in model_features:
    ic = daily_rank_ic(df, f, target_col)
    if len(ic) >= 10:
        ic_stats[f] = ic_summary(ic, HORIZON)
ic_mean = pd.Series({f: s["mean_ic"] for f, s in ic_stats.items()})
ic_t = pd.Series({f: s["t_nw"] for f, s in ic_stats.items()})

comparison_df = pd.DataFrame(
    {
        "Mean_IC": ic_mean,
        "IC_t_NW": ic_t,
        "IC_Abs": ic_mean.abs(),
        "RF_Permutation": perm_importances,
        "LASSO_Coef_Abs": lasso_coefs.abs(),
    }
)

# Create rankings (1 = most important)
comparison_df["Rank_IC"] = comparison_df["IC_Abs"].rank(ascending=False)
comparison_df["Rank_RF_Perm"] = comparison_df["RF_Permutation"].rank(ascending=False)
comparison_df["Rank_LASSO"] = comparison_df["LASSO_Coef_Abs"].rank(ascending=False)
rank_cols = ["Rank_IC", "Rank_RF_Perm", "Rank_LASSO"]
comparison_df["Average_Rank"] = comparison_df[rank_cols].mean(axis=1)
comparison_df = comparison_df.sort_values("Average_Rank")

print("Top 15 Features by Consensus (Average Rank):")
display(comparison_df.head(15)[["Mean_IC", "IC_t_NW", *rank_cols, "Average_Rank"]])

# Highlight disagreements (High variance in ranks)
comparison_df["Rank_Std"] = comparison_df[rank_cols].std(axis=1)
print("\nTop 5 Features with Highest Disagreement (Std Dev of Ranks):")
display(
    comparison_df.sort_values("Rank_Std", ascending=False).head(5)[
        [*rank_cols, "Rank_Std"]
    ]
)

# %% [markdown]
# ## 5. Hypothesis Testing (Hypothesentests)
#
# Systematically testing hypotheses from `LEARNINGS_HYPOTHESES.md` on the full window
# (excess `return_20d`):
# - group hypotheses: per-date mean difference (dates with >= 3 members in both groups),
#   Newey-West t (lag 19) + date-block bootstrap CI (block = 20 dates);
# - correlation hypotheses: difference of daily rank ICs, Newey-West t.

# %%
results = []
LAG = nw_lag_for_horizon(HORIZON)


def _verdict(p_val, effect):
    if np.isnan(p_val):
        return "Inconclusive (Not enough data)"
    if p_val < 0.05 and effect > 0:
        return "Confirmed"
    if p_val < 0.05 and effect < 0:
        return "Rejected (Opposite effect)"
    return "Inconclusive (Not significant)"


def run_group_test(name, desc, mask, valid):
    """Per-date difference of mean excess return between group A (mask) and B."""
    diff = daily_group_difference(
        df, mask.astype(float).where(valid), target_col, min_group=3
    )
    if len(diff) < 20:
        return {
            "Hypothesis": name,
            "Description": desc,
            "p-value": np.nan,
            "T-stat (NW)": np.nan,
            "Verdict": "Inconclusive (Not enough data)",
        }
    t = mean_test(diff, LAG)
    lo, hi = bootstrap_ci(date_block_bootstrap(diff.values, HORIZON, 1000, seed=42))
    return {
        "Hypothesis": name,
        "Description": desc,
        "Effect Size": round(t["mean"], 4),
        "95% CI (block bootstrap)": f"[{lo:.4f}, {hi:.4f}]",
        "T-stat (NW)": round(t["t_nw"], 2),
        "p-value": round(t["pvalue"], 4),
        "Dates": t["n"],
        "Verdict": _verdict(t["pvalue"], t["mean"]),
    }


def run_ic_test(name, desc, feat_a, feat_b, use_abs=False):
    """Difference of daily rank ICs (sign-adjusted for |IC| comparisons)."""
    ic_a, ic_b = ic_difference(df, feat_a, feat_b, target_col)
    if len(ic_a) < 20:
        return {
            "Hypothesis": name,
            "Description": desc,
            "p-value": np.nan,
            "T-stat (NW)": np.nan,
            "Verdict": "Inconclusive (Not enough data)",
        }
    if use_abs:
        diff = (np.sign(ic_a.mean()) or 1) * ic_a - (np.sign(ic_b.mean()) or 1) * ic_b
    else:
        diff = ic_a - ic_b
    t = mean_test(diff, LAG)
    return {
        "Hypothesis": name,
        "Description": desc,
        "Mean A": round(ic_a.mean(), 4),
        "Mean B": round(ic_b.mean(), 4),
        "Effect Size": round(t["mean"], 4),
        "T-stat (NW)": round(t["t_nw"], 2),
        "p-value": round(t["pvalue"], 4),
        "Dates": t["n"],
        "Verdict": _verdict(t["pvalue"], t["mean"]),
    }


def col(name):
    return df[name] if name in df.columns else pd.Series(np.nan, index=df.index)


# H1: ARK Multi-ETF
x = col("ark_multi_etf_signal")
results.append(
    run_group_test(
        "H1 (ARK Multi-ETF)", "ark_multi_etf_signal=1 vs 0", x == 1, x.notna()
    )
)

# H2: Insider Cluster > Single Buys (daily IC comparison)
results.append(
    run_ic_test(
        "H2 (Insider Cluster > Single)",
        "IC(Cluster) - IC(Single)",
        "insider_cluster_score",
        "insider_buy_value_30d",
    )
)

# H3: ARK + Form4 Combined
a, b = col("ark_conviction_score"), col("insider_cluster_active")
results.append(
    run_group_test(
        "H3 (ARK + Form4)",
        "Combined signal vs rest",
        (a > 0) & (b == 1),
        a.notna() & b.notna(),
    )
)

# H4: Weight > Shares
results.append(
    run_ic_test(
        "H4 (Weight > Shares)",
        "IC(WeightDelta) - IC(Conviction)",
        "ark_weight_delta_20d",
        "ark_conviction_score",
    )
)

# H9: Downgrades > Upgrades
results.append(
    run_ic_test(
        "H9 (Downgrades > Upgrades)",
        "|IC(Down)| - |IC(Up)|",
        "analyst_downgrades_30d",
        "analyst_upgrades_30d",
        use_abs=True,
    )
)

# H10: Insider after Earnings Drop
# consecutive_beats == 0: ticker has earnings coverage but no current beat streak
# (NULL only without coverage).
ica, cb = col("insider_cluster_active"), col("consecutive_beats")
results.append(
    run_group_test(
        "H10 (Insider Earnings Drop)",
        "Insider cluster + no beat streak vs rest",
        (ica == 1) & (cb == 0),
        ica.notna() & cb.notna(),
    )
)

# H11: Recurring Clusters
c = col("cluster_count_60d")
results.append(
    run_group_test(
        "H11 (Recurring Clusters)", "cluster_count_60d > 1 vs == 1", c > 1, c >= 1
    )
)

# H12: Persistent ARK
s = col("ark_conviction_streak")
results.append(
    run_group_test(
        "H12 (Persistent ARK)", "ark_conviction_streak > 3 vs 1..3", s > 3, s > 0
    )
)

# H13: Multi-Source Convergence
# analyst_rating_score lies in [-1, 1] -> "positive consensus" is > 0 (old bug: > 3.5).
active_sources = (
    (col("ark_conviction_score") > 0).astype(int)
    + (col("insider_cluster_active") == 1).astype(int)
    + (col("analyst_rating_score") > 0).astype(int)
    + (col("politician_buy_count_60d_disclosure") > 0).astype(int)
)
results.append(
    run_group_test(
        "H13 (Multi-Source)",
        "Sources >= 2 vs Sources < 2",
        active_sources >= 2,
        pd.Series(True, index=df.index),
    )
)

# Display Hypothesis Results
results_df = pd.DataFrame(results)
display(results_df)

# %% [markdown]
# ## 6. Sprint 10 Recommendations (Empfehlungen für Sprint 10)
#
# > **Important:** The previous recommendations in this section (feature shortlist,
# > exclusions and rough weights such as "ARK 30% / Insider 35% / ...") were derived
# > from
# > the old, biased methodology (pooled correlations, leaky CV, row-iid tests, missing
# > features, wrong target timing). They are **withdrawn**.
#
# Conclusions must be **re-derived after the feature/target rebuild** (new target
# definition `close(d+h)/open(d+1) - 1`, point-in-time politician transaction features,
# `feature_version` >= 2026.10-1) by re-running notebooks 02 and 03. Fill in below:
#
# ### Final Feature Shortlist
# *Features with a significant mean daily IC (Bonferroni, NW t), consistent sign across
# months/folds, positive OOS IC contribution in purged CV and agreement across methods.*
# - _to be filled after the rebuild_
#
# ### Features to Exclude or Downweight
# *Features with insignificant ICs, zero LASSO coefficients, negative permutation
# importance
# or high redundancy (|rank corr| > 0.7) with a stronger feature.*
# - _to be filled after the rebuild_
#
# ### Weight Recommendations (for scoring logic)
# - Derive from OOS evidence (e.g. IC-weighted or regularised model), never from
#   in-sample
#   pooled correlations. Apply weights to per-date ranks, not raw values.
#
# ### Open Questions
# - Interaction features (e.g., ARK buy + Insider buy): additive bonuses or
#   multiplicative?
# - Separate models per market-cap bucket (coverage of analysts/insiders differs)?
# - Market-wide features (macro_*, breadth_*) need a separate time-series / regime
#   analysis;
#   they carry no cross-sectional information.
