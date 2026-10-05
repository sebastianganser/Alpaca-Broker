# %% [markdown]
# # Sprint 9 (rebuilt): Feature-Return Correlations – Daily Rank IC
# This notebook analyzes the predictive power of all model features across the return
# horizons using **cross-sectional daily rank ICs** (Spearman per date), Newey-West
# t-statistics (overlapping targets) and per-date quintile portfolios.
#
# **Why rebuilt?** The previous version used pooled Spearman correlations over all rows
# (mixing market-wide moves with cross-sectional signal and treating ~700 rows per day
# as
# independent), row-iid bootstraps and hard-coded feature lists. Those results were
# biased
# and must not be used.
#
# Methodology:
# - Window: `snapshot_date >= ml_start_date()` (warmed-up indicators only).
# - Features: `FEATURE_COLUMNS` from the ORM model (single source of truth).
# - Targets: `return_h = close(d+h) / open(d+1) - 1`, converted to **excess returns**
#   (minus the per-date cross-sectional mean).
# - Inference: mean daily IC, ICIR, Newey-West t with lag `h-1`, Bonferroni over all
#   tests.

# %% [markdown]
# ## 1. Data Loading & Preparation / Daten laden & vorbereiten
# Only the needed columns are loaded (keys + features + targets), parameterised by the
# ML
# start date.

# %%
import os
import sys
import warnings

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

warnings.filterwarnings("ignore")

# Set plotting style
sns.set_theme(style="whitegrid")
plt.rcParams["figure.figsize"] = (12, 8)

# Connect to database
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath("__file__")), "..", "src")
)
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy import inspect as sa_inspect  # noqa: E402

from trading_signals.analysis.feature_groups import feature_group_of  # noqa: E402
from trading_signals.analysis.feature_report import (  # noqa: E402
    FEATURE_GROUPS,
    build_load_query,
    prepare_frame,
)
from trading_signals.analysis.stats import (  # noqa: E402
    available_features,
    bootstrap_ci,
    cross_sectional_transform,
    daily_rank_ic,
    date_block_bootstrap,
    horizon_from_target,
    ic_difference,
    ic_summary,
    mean_test,
    nw_lag_for_horizon,
    quintile_returns,
)
from trading_signals.config import get_settings  # noqa: E402
from trading_signals.db.models.features import TARGET_COLUMNS  # noqa: E402
from trading_signals.utils.retention import ml_start_date  # noqa: E402

print("Connecting to database...")
settings = get_settings()
engine = create_engine(settings.database_url)

start = ml_start_date()
table_cols = [
    c["name"]
    for c in sa_inspect(engine).get_columns("feature_snapshots", schema="signals")
]
sql, params = build_load_query(table_cols, start)
print(f"Loading signals.feature_snapshots from {start} (explicit columns)...")
raw = pd.read_sql(sql, engine, params=params)

# bool -> float, targets -> excess returns (minus per-date cross-sectional mean)
df = prepare_frame(raw)
del raw

print(
    f"Loaded {len(df)} rows, {df['ticker'].nunique()} tickers, "
    f"{df['snapshot_date'].nunique()} dates "
    f"({df['snapshot_date'].min().date()} to {df['snapshot_date'].max().date()})."
)

available = available_features(df)
available_targets = [
    t for t in TARGET_COLUMNS if t in df.columns and df[t].notna().any()
]
horizons = {t: horizon_from_target(t) for t in available_targets}

print(f"Available features for analysis: {len(available)}")
print(f"Available targets for analysis: {available_targets}")

# %% [markdown]
# ## 2. Daily Rank IC Matrix (Feature -> Excess Return) / Tägliche Rang-IC-Matrix
# For every feature x target: Spearman correlation per date (min. 10 tickers, constant
# dates skipped), then mean IC, ICIR, Newey-West t (lag `h-1`) and Bonferroni flag.
# Market-wide features (macro_*, breadth_*) have no cross-sectional variation and drop
# out.

# %%
print("--- Section 2: Daily Rank IC Matrix ---")

daily_ics = {}  # (feature, target) -> daily IC series
rows = []
for target in available_targets:
    for feature in available:
        ic = daily_rank_ic(df, feature, target, min_obs=10)
        if len(ic) < 10:
            continue
        daily_ics[(feature, target)] = ic
        s = ic_summary(ic, horizons[target])
        rows.append({"Feature": feature, "Target": target, **s})

ic_df = pd.DataFrame(rows)
n_tests = len(ic_df)
ic_df["Significant"] = ic_df["pvalue"] * max(n_tests, 1) < 0.05
ic_df["Group"] = ic_df["Feature"].map(feature_group_of)
alpha_bonf = 0.05 / max(n_tests, 1)
print(f"{n_tests} feature x target tests (Bonferroni alpha = {alpha_bonf:.2e})")

if not ic_df.empty:
    pivot_ic = ic_df.pivot(index="Feature", columns="Target", values="mean_ic")
    pivot_t = ic_df.pivot(index="Feature", columns="Target", values="t_nw")
    fig, axes = plt.subplots(1, 2, figsize=(20, max(8, 0.3 * len(pivot_ic))))
    sns.heatmap(
        pivot_ic,
        cmap="RdBu",
        center=0,
        annot=True,
        fmt=".3f",
        ax=axes[0],
        cbar_kws={"label": "Mean daily rank IC"},
    )
    axes[0].set_title("Mean daily rank IC (excess returns)")
    sns.heatmap(
        pivot_t,
        cmap="RdBu",
        center=0,
        annot=True,
        fmt=".1f",
        ax=axes[1],
        vmin=-5,
        vmax=5,
        cbar_kws={"label": "t (Newey-West)"},
    )
    axes[1].set_title("Newey-West t-stat of mean IC")
    plt.tight_layout()
    plt.show()
    display_cols = [
        "Feature",
        "Target",
        "mean_ic",
        "icir",
        "t_nw",
        "pvalue",
        "n_dates",
        "Significant",
    ]
    print(
        ic_df.sort_values("t_nw", key=abs, ascending=False)[display_cols]
        .head(30)
        .to_string(index=False)
    )
else:
    print("Not enough data to compute daily ICs.")

# %% [markdown]
# ## 3. Top Features per Return Horizon / Top Features pro Horizont
# Rank features by |t_NW| per horizon. Confidence intervals for the mean IC come from a
# **moving block bootstrap over dates** (block length = max(h, 5)), not from resampling
# rows.

# %%
print("--- Section 3: Top Features per Return Horizon ---")

top_n = 15
top_features_per_target = {}

for target in available_targets:
    target_df = ic_df[ic_df["Target"] == target].copy()
    if target_df.empty:
        continue
    top_target = target_df.reindex(
        target_df["t_nw"].abs().sort_values(ascending=False).index
    ).head(top_n)
    top_features_per_target[target] = top_target["Feature"].tolist()

    plt.figure(figsize=(12, 8))
    colors = ["#2ecc71" if sig else "#95a5a6" for sig in top_target["Significant"]]
    plt.barh(top_target["Feature"], top_target["mean_ic"], color=colors)
    plt.gca().invert_yaxis()
    plt.title(
        f"Top {top_n} Features for {target} by |t_NW| "
        "(Green = Significant after Bonferroni)",
        fontsize=14,
    )
    plt.xlabel("Mean daily rank IC")
    plt.tight_layout()
    plt.show()

    block = max(horizons[target], 5)
    print(
        f"\nTop 10 features for {target} – date-block bootstrap 95% CI "
        f"(block={block} dates, 1000 iter):"
    )
    for feature in top_target["Feature"].head(10):
        ic = daily_ics[(feature, target)]
        lo, hi = bootstrap_ci(date_block_bootstrap(ic.values, block, 1000, seed=42))
        print(f"  {feature}: IC = {ic.mean():.4f} [95% CI: {lo:.4f}, {hi:.4f}]")

# %% [markdown]
# ## 4. Feature Group Analysis / Feature-Gruppen Analyse
# Average |mean daily IC| per feature group (groups derived from the model by name
# prefix).

# %%
print("--- Section 4: Feature Group Analysis ---")
print("Groups:", {g: len(fs) for g, fs in FEATURE_GROUPS.items()})

if not ic_df.empty:
    ic_df["Abs_IC"] = ic_df["mean_ic"].abs()
    group_stats = ic_df.groupby(["Group", "Target"])["Abs_IC"].mean().reset_index()

    plt.figure(figsize=(14, 8))
    sns.barplot(x="Group", y="Abs_IC", hue="Target", data=group_stats)
    plt.title("Average |mean daily rank IC| per Feature Group", fontsize=16)
    plt.xlabel("Feature Group")
    plt.ylabel("Mean |IC|")
    plt.xticks(rotation=45)
    plt.legend(title="Return Horizon")
    plt.tight_layout()
    plt.show()

    print("Which signal source has the highest overall predictive power?")
    overall = group_stats.groupby("Group")["Abs_IC"].mean().sort_values(ascending=False)
    for i, (group, val) in enumerate(overall.items(), 1):
        print(f"{i}. {group} (Mean |IC| = {val:.4f})")

# %% [markdown]
# ## 5. Politician Dual-Date Evaluation (Hypothesis H7) / Politiker Dual-Datum
# Evaluation
# Compare `_disclosure` vs `_transaction` variants. Both are point-in-time now
# (transaction variants only count trades with `disclosure_date <= d`). The difference
# of
# daily ICs is tested with a Newey-West t-stat.

# %%
print("--- Section 5: Politician Dual-Date Evaluation ---")

pol_bases = sorted(
    {
        f.replace("_disclosure", "").replace("_transaction", "")
        for f in available
        if f.startswith("politician")
    }
)
for base in pol_bases:
    disc, trans = f"{base}_disclosure", f"{base}_transaction"
    if disc not in available or trans not in available:
        continue
    for target in available_targets:
        ic_d, ic_t = ic_difference(df, disc, trans, target)
        if len(ic_d) < 10:
            continue
        t = mean_test(ic_d - ic_t, nw_lag_for_horizon(horizons[target]))
        winner = "Disclosure" if abs(ic_d.mean()) > abs(ic_t.mean()) else "Transaction"
        print(
            f"  {base} ({target}): Disclosure IC = {ic_d.mean():.4f}, "
            f"Transaction IC = {ic_t.mean():.4f}, diff t_NW = {t['t_nw']:.2f} "
            f"-> larger |IC|: {winner}"
        )

# %% [markdown]
# ## 6. Quintile Analysis for Top Features / Quintils-Analyse für Top Features
# Quintiles are assigned **per date** (ties share a bucket), mean excess return per
# (date, quintile), then averaged across dates. Long-short = top minus bottom non-empty
# bucket per date, Newey-West t.

# %%
print("--- Section 6: Quintile Analysis ---")

if "return_20d" in top_features_per_target:
    top_5_features = top_features_per_target["return_20d"][:5]

    fig, axes = plt.subplots(
        len(top_5_features), 1, figsize=(10, 4 * len(top_5_features)), squeeze=False
    )
    for ax, feature in zip(axes[:, 0], top_5_features, strict=False):
        q = quintile_returns(df, feature, "return_20d", horizon=20)
        qm = q["quantile_means"]
        ax.bar([f"Q{i}" for i in qm.index], qm.values, color="steelblue")
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_title(
            f"Mean 20d excess return by per-date {feature} quintile | "
            f"L/S = {q['long_short_mean']:.4f} "
            f"(t_NW = {q['long_short_t_nw']:.2f}, {q['n_dates']} dates)",
            fontsize=11,
        )
        ax.set_ylabel("Mean excess return_20d")
    plt.tight_layout()
    plt.show()

# %% [markdown]
# ## 7. Temporal Stability / Zeitliche Stabilität
# Monthly mean of the daily IC (and a 60-day rolling mean) for the top features.

# %%
print("--- Section 7: Temporal Stability ---")

if "return_20d" in top_features_per_target:
    top_3_features = top_features_per_target["return_20d"][:3]
    fig, axes = plt.subplots(2, 1, figsize=(14, 10))
    for feature in top_3_features:
        ic = daily_ics[(feature, "return_20d")]
        monthly = ic.groupby(ic.index.to_period("M")).mean()
        axes[0].plot(
            monthly.index.astype(str), monthly.values, marker="o", label=feature
        )
        axes[1].plot(ic.index, ic.rolling(60, min_periods=20).mean(), label=feature)
        share_pos = (monthly > 0).mean()
        print(
            f"  {feature}: {share_pos:.0%} of months with positive mean IC "
            f"({len(monthly)} months)"
        )
    axes[0].set_title("Monthly mean daily rank IC (feature vs excess return_20d)")
    axes[0].tick_params(axis="x", rotation=45)
    axes[1].set_title("60-day rolling mean daily rank IC")
    for ax in axes:
        ax.axhline(0, color="black", linestyle="--")
        ax.legend()
    plt.tight_layout()
    plt.show()

# %% [markdown]
# ## 8. Inter-Feature Correlations (Multicollinearity) / Inter-Feature Korrelationen
# Features are ranked **per date** first (uniform [0, 1]); the correlation of these
# ranks
# measures cross-sectional redundancy without being driven by common time trends.

# %%
print("--- Section 8: Inter-Feature Correlations ---")

ranked = cross_sectional_transform(
    df[["snapshot_date", *available]], available, "rank", dtype="float32"
)
# drop features without cross-sectional variation (market-wide: all ranks 0.5)
cs_features = [f for f in available if (ranked[f].dropna() != 0.5).any()]
feat_corr_matrix = ranked[cs_features].corr()

cluster_grid = sns.clustermap(
    feat_corr_matrix.fillna(0),
    cmap="coolwarm",
    center=0,
    figsize=(16, 16),
    dendrogram_ratio=0.15,
    cbar_pos=(0.02, 0.8, 0.03, 0.18),
)
cluster_grid.fig.suptitle(
    "Inter-Feature Correlation of per-date ranks (Hierarchical Clustering)",
    fontsize=16,
    y=1.02,
)
plt.show()

print("\nHighly Correlated Feature Pairs (|rho| > 0.70):")
upper_tri = feat_corr_matrix.where(
    np.triu(np.ones(feat_corr_matrix.shape), k=1).astype(bool)
)
high_corr = (
    upper_tri.stack()
    .rename("Correlation")
    .reset_index()
    .rename(columns={"level_0": "Feature_1", "level_1": "Feature_2"})
)
high_corr = high_corr[high_corr["Correlation"].abs() > 0.7]

if not high_corr.empty:
    for _, r in high_corr.sort_values(
        "Correlation", key=abs, ascending=False
    ).iterrows():
        print(f"  {r['Feature_1']} <-> {r['Feature_2']}: {r['Correlation']:.4f}")
    print("\nRecommendation for feature reduction:")
    print(
        "Consider removing one feature from each highly correlated pair "
        "or using PCA within groups."
    )
else:
    print("No feature pairs with |rho| > 0.70 found.")
