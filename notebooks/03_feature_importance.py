# %% [markdown]
# # Sprint 9: Feature Importance & Hypothesis Testing (Exploratory Analysis)
# 
# This notebook conducts a comprehensive feature importance analysis on the calculated features and tests specific hypotheses regarding trading signals.
# 
# ## Goals:
# 1. Evaluate feature importance using Random Forest (MDI and Permutation) and LASSO Regression.
# 2. Compare rankings across methods and Spearman correlation.
# 3. Test specific hypotheses defined in `LEARNINGS_HYPOTHESES.md`.
# 4. Synthesize findings into recommendations for the final scoring model (Sprint 10).

# %%
# Import libraries
import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from sklearn.ensemble import RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LassoCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error

# Set plotting style
plt.style.use('seaborn-v0_8-whitegrid')
sns.set_context("notebook", font_scale=1.1)

# Add src directory to path for config
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath('__file__')), '..', 'src'))
from trading_signals.config import get_settings
from sqlalchemy import create_engine

# %% [markdown]
# ## 1. Daten laden und aufbereiten (Data Loading & Preparation)
# 
# - We load data from `signals.feature_snapshots`
# - Exclude 13F features (no data) and `return_60d`
# - Filter to target `return_20d` IS NOT NULL
# - Chronological Train/Test Split (70/30)
# - Missing value imputation (Median) for models

# %%
# Load data from database
print("Loading data from database...")
settings = get_settings()
engine = create_engine(settings.database_url)
query = """
SELECT * FROM signals.feature_snapshots 
ORDER BY snapshot_date, ticker
"""
df = pd.read_sql(query, engine)

print(f"Total rows loaded: {len(df)}")
print(f"Date range: {df['snapshot_date'].min()} to {df['snapshot_date'].max()}")

# Define feature columns
ark_features = [
    'ark_in_etf_count', 'ark_total_weight', 'ark_weight_delta_1d', 
    'ark_weight_delta_5d', 'ark_weight_delta_20d', 'ark_conviction_score', 
    'ark_multi_etf_signal', 'ark_increase_days_10d', 'ark_increase_days_20d', 
    'ark_conviction_streak', 'ark_weight_trend_20d'
]

insider_features = [
    'insider_net_buy_count_30d', 'insider_buy_value_30d', 'insider_cluster_active', 
    'insider_cluster_score', 'cluster_count_30d', 'cluster_count_60d', 
    'cluster_score_sum_60d', 'days_since_last_cluster'
]

analyst_features = [
    'analyst_rating_score', 'analyst_upgrades_30d', 'analyst_price_target_upside', 
    'analyst_downgrades_30d', 'analyst_net_sentiment_30d', 'analyst_net_sentiment_60d', 
    'analyst_upgrade_streak'
]

politician_features = [
    'politician_buy_count_60d_disclosure', 'politician_distinct_90d_disclosure', 
    'politician_buy_count_60d_transaction', 'politician_distinct_90d_transaction'
]

fundamental_features = [
    'pe_ratio', 'forward_pe', 'ps_ratio', 'revenue_growth_yoy', 
    'profit_margin', 'debt_to_equity', 'pe_trend_4w', 'margin_trend_4w'
]

technical_features = [
    'price_vs_sma50', 'price_vs_sma200', 'rsi_14', 'relative_strength_spy', 
    'volume_ratio_20d', 'atr_14_pct'
]

earnings_features = [
    'earnings_days_until', 'consecutive_beats', 'surprise_trend_3q'
]

sentiment_features = [
    'sentiment_avg_7d', 'sentiment_avg_30d', 'sentiment_momentum', 
    'sentiment_neg_count_7d', 'sentiment_article_count_7d', 'market_sentiment_7d'
]

all_features = (
    ark_features + insider_features + analyst_features + politician_features + 
    fundamental_features + technical_features + earnings_features + sentiment_features
)

print(f"Total features defined: {len(all_features)}")

# Convert boolean features to int
bool_cols = df[all_features].select_dtypes(include=['bool']).columns
for col in bool_cols:
    df[col] = df[col].astype(int)

# Target variables
targets = ['return_1d', 'return_5d', 'return_20d']
target_col = 'return_20d'

# Filter target
df_clean = df.dropna(subset=[target_col]).copy()
print(f"Rows after dropping null {target_col}: {len(df_clean)}")

# Chronological Train/Test Split
unique_dates = df_clean['snapshot_date'].sort_values().unique()
split_idx = int(len(unique_dates) * 0.7)
train_dates = unique_dates[:split_idx]
test_dates = unique_dates[split_idx:]

train_mask = df_clean['snapshot_date'].isin(train_dates)
test_mask = df_clean['snapshot_date'].isin(test_dates)

train_df = df_clean[train_mask].copy()
test_df = df_clean[test_mask].copy()

print(f"Train size: {len(train_df)} ({len(train_dates)} dates)")
print(f"Test size: {len(test_df)} ({len(test_dates)} dates)")

# Imputation with median (fit on train, apply to train and test)
feature_medians = train_df[all_features].median()

X_train = train_df[all_features].fillna(feature_medians)
y_train = train_df[target_col]

X_test = test_df[all_features].fillna(feature_medians)
y_test = test_df[target_col]

print("Data preparation complete.")

# %% [markdown]
# ## 2. Random Forest Feature Importance (Wichtigkeitsanalyse)
# 
# We train a RandomForestRegressor on `return_20d`.
# We examine both Impurity-based (MDI) and Permutation importance.

# %%
print("Training Random Forest Regressor...")
rf = RandomForestRegressor(
    n_estimators=500, 
    max_depth=10, 
    min_samples_leaf=20, 
    random_state=42, 
    n_jobs=-1
)
rf.fit(X_train, y_train)

# Evaluate on test set
y_pred = rf.predict(X_test)
r2 = r2_score(y_test, y_pred)
mae = mean_absolute_error(y_test, y_pred)
print(f"Random Forest Performance (return_20d): R2 = {r2:.4f}, MAE = {mae:.4f}")

# a) MDI Importance
mdi_importances = pd.Series(rf.feature_importances_, index=all_features).sort_values(ascending=False)

plt.figure(figsize=(12, 10))
sns.barplot(x=mdi_importances.head(20).values, y=mdi_importances.head(20).index, palette='viridis')
plt.title("Top 20 Features: Random Forest Impurity-based Importance (MDI)")
plt.xlabel("Gini Importance")
plt.tight_layout()
plt.show()

# b) Permutation Importance
print("Calculating Permutation Importance (may take a minute)...")
perm_result = permutation_importance(rf, X_test, y_test, n_repeats=5, random_state=42, n_jobs=-1)
perm_importances = pd.Series(perm_result.importances_mean, index=all_features).sort_values(ascending=False)

plt.figure(figsize=(12, 10))
sns.barplot(x=perm_importances.head(20).values, y=perm_importances.head(20).index, palette='magma')
plt.title("Top 20 Features: Random Forest Permutation Importance")
plt.xlabel("Mean Accuracy Decrease")
plt.tight_layout()
plt.show()

# %% [markdown]
# ### Evaluate for different horizons (return_1d, return_5d)
# We will quickly train models for shorter horizons to see if importance rankings shift.

# %%
for tgt in ['return_1d', 'return_5d']:
    print(f"\nAnalyzing target: {tgt}")
    df_tgt = df.dropna(subset=[tgt]).copy()
    tr_df = df_tgt[df_tgt['snapshot_date'].isin(train_dates)].copy()
    te_df = df_tgt[df_tgt['snapshot_date'].isin(test_dates)].copy()
    
    # Impute
    med = tr_df[all_features].median()
    X_tr = tr_df[all_features].fillna(med)
    y_tr = tr_df[tgt]
    X_te = te_df[all_features].fillna(med)
    y_te = te_df[tgt]
    
    rf_tgt = RandomForestRegressor(n_estimators=100, max_depth=10, min_samples_leaf=20, random_state=42, n_jobs=-1)
    rf_tgt.fit(X_tr, y_tr)
    
    mdi_tgt = pd.Series(rf_tgt.feature_importances_, index=all_features).sort_values(ascending=False)
    print(f"Top 5 Features for {tgt} (MDI):")
    print(mdi_tgt.head(5))

# %% [markdown]
# ## 3. LASSO Regression (LASSO-Modellierung & Selektion)
# 
# LASSO shrinks less important feature coefficients to exactly zero, providing intrinsic feature selection.

# %%
print("Scaling features for LASSO...")
scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_test_scaled = scaler.transform(X_test)

print("Training LASSO with TimeSeriesSplit CV...")
tscv = TimeSeriesSplit(n_splits=5)
lasso = LassoCV(cv=tscv, random_state=42, n_jobs=-1, max_iter=10000)
lasso.fit(X_train_scaled, y_train)

print(f"Optimal Alpha: {lasso.alpha_:.6f}")

# Extract coefficients
lasso_coefs = pd.Series(lasso.coef_, index=all_features)
non_zero_coefs = lasso_coefs[lasso_coefs != 0].sort_values(key=abs, ascending=False)
zero_features = lasso_coefs[lasso_coefs == 0].index.tolist()

print(f"\nLASSO retained {len(non_zero_coefs)} features and zeroed out {len(zero_features)} features.")

plt.figure(figsize=(10, 8))
sns.barplot(x=non_zero_coefs.head(20).values, y=non_zero_coefs.head(20).index, palette='coolwarm')
plt.title("Top 20 Non-Zero LASSO Coefficients")
plt.xlabel("Coefficient Value")
plt.tight_layout()
plt.show()

# Evaluate LASSO performance
y_pred_lasso = lasso.predict(X_test_scaled)
print(f"LASSO Performance (return_20d): R2 = {r2_score(y_test, y_pred_lasso):.4f}, MAE = {mean_absolute_error(y_test, y_pred_lasso):.4f}")

# %% [markdown]
# ## 4. Method Comparison (Vergleich der Methoden)
# 
# Compare Rankings: Spearman ρ vs Random Forest Permutation vs LASSO Magnitude.

# %%
# Calculate Spearman Correlation
spearman_corr = train_df[all_features + [target_col]].corr(method='spearman')[target_col].drop(target_col)

# Create comparison dataframe
comparison_df = pd.DataFrame({
    'Spearman_Rho': spearman_corr,
    'Spearman_Abs': spearman_corr.abs(),
    'RF_Permutation': perm_importances,
    'LASSO_Coef_Abs': lasso_coefs.abs()
})

# Create rankings (1 = most important)
comparison_df['Rank_Spearman'] = comparison_df['Spearman_Abs'].rank(ascending=False)
comparison_df['Rank_RF_Perm'] = comparison_df['RF_Permutation'].rank(ascending=False)
comparison_df['Rank_LASSO'] = comparison_df['LASSO_Coef_Abs'].rank(ascending=False)
comparison_df['Average_Rank'] = comparison_df[['Rank_Spearman', 'Rank_RF_Perm', 'Rank_LASSO']].mean(axis=1)

comparison_df = comparison_df.sort_values('Average_Rank')

print("Top 15 Features by Consensus (Average Rank):")
display(comparison_df.head(15)[['Rank_Spearman', 'Rank_RF_Perm', 'Rank_LASSO', 'Average_Rank']])

# Highlight disagreements (High variance in ranks)
comparison_df['Rank_Std'] = comparison_df[['Rank_Spearman', 'Rank_RF_Perm', 'Rank_LASSO']].std(axis=1)
print("\nTop 5 Features with Highest Disagreement (Std Dev of Ranks):")
display(comparison_df.sort_values('Rank_Std', ascending=False).head(5)[['Rank_Spearman', 'Rank_RF_Perm', 'Rank_LASSO', 'Rank_Std']])

# %% [markdown]
# ## 5. Hypothesis Testing (Hypothesentests)
# 
# Systematically testing hypotheses from `LEARNINGS_HYPOTHESES.md`.

# %%
results = []

def run_hypothesis_test(name, desc, group_a, group_b, alternative='two-sided'):
    group_a_clean = group_a.dropna()
    group_b_clean = group_b.dropna()
    
    if len(group_a_clean) < 30 or len(group_b_clean) < 30:
        return {'Hypothesis': name, 'Description': desc, 'p-value': np.nan, 'T-stat': np.nan, 'Verdict': 'Inconclusive (Not enough data)'}
        
    t_stat, p_val = stats.ttest_ind(group_a_clean, group_b_clean, alternative=alternative, equal_var=False)
    
    mean_a = group_a_clean.mean()
    mean_b = group_b_clean.mean()
    effect_size = mean_a - mean_b
    
    if p_val < 0.05 and effect_size > 0:
        verdict = 'Confirmed'
    elif p_val < 0.05 and effect_size < 0:
        verdict = 'Rejected (Opposite effect)'
    else:
        verdict = 'Inconclusive (Not significant)'
        
    return {
        'Hypothesis': name,
        'Description': desc,
        'Mean A': round(mean_a, 4),
        'Mean B': round(mean_b, 4),
        'Effect Size': round(effect_size, 4),
        'T-stat': round(t_stat, 2),
        'p-value': round(p_val, 4),
        'Verdict': verdict
    }

df_hyp = train_df.copy()

# H1: ARK Multi-ETF
res = run_hypothesis_test(
    'H1 (ARK Multi-ETF)', 
    'ark_multi_etf_signal=1 vs 0',
    df_hyp[df_hyp['ark_multi_etf_signal'] == 1][target_col],
    df_hyp[df_hyp['ark_multi_etf_signal'] == 0][target_col],
    alternative='greater'
)
results.append(res)

# H2: Insider Cluster > Single Buys
# Compare spearman correlation instead of t-test
h2_cluster_corr = df_hyp[['insider_cluster_score', target_col]].corr(method='spearman').iloc[0,1]
h2_single_corr = df_hyp[['insider_buy_value_30d', target_col]].corr(method='spearman').iloc[0,1]
results.append({
    'Hypothesis': 'H2 (Insider Cluster > Single)',
    'Description': 'Corr(Cluster) vs Corr(Single)',
    'Mean A': round(h2_cluster_corr, 4),
    'Mean B': round(h2_single_corr, 4),
    'Effect Size': round(h2_cluster_corr - h2_single_corr, 4),
    'T-stat': np.nan, 'p-value': np.nan,
    'Verdict': 'Confirmed' if h2_cluster_corr > h2_single_corr else 'Rejected'
})

# H3: ARK + Form4 Combined
df_hyp['h3_combo'] = ((df_hyp['ark_conviction_score'] > 0) & (df_hyp['insider_cluster_active'] == 1)).astype(int)
res = run_hypothesis_test(
    'H3 (ARK + Form4)', 
    'Combined signal vs None',
    df_hyp[df_hyp['h3_combo'] == 1][target_col],
    df_hyp[df_hyp['h3_combo'] == 0][target_col],
    alternative='greater'
)
results.append(res)

# H4: Weight > Shares
h4_weight_corr = df_hyp[['ark_weight_delta_20d', target_col]].corr(method='spearman').iloc[0,1]
h4_conv_corr = df_hyp[['ark_conviction_score', target_col]].corr(method='spearman').iloc[0,1]
results.append({
    'Hypothesis': 'H4 (Weight > Shares)',
    'Description': 'Corr(WeightDelta) vs Corr(Conviction)',
    'Mean A': round(h4_weight_corr, 4),
    'Mean B': round(h4_conv_corr, 4),
    'Effect Size': round(h4_weight_corr - h4_conv_corr, 4),
    'T-stat': np.nan, 'p-value': np.nan,
    'Verdict': 'Confirmed' if h4_weight_corr > h4_conv_corr else 'Rejected'
})

# H9: Downgrades > Upgrades
h9_down_corr = df_hyp[['analyst_downgrades_30d', target_col]].corr(method='spearman').iloc[0,1]
h9_up_corr = df_hyp[['analyst_upgrades_30d', target_col]].corr(method='spearman').iloc[0,1]
results.append({
    'Hypothesis': 'H9 (Downgrades > Upgrades)',
    'Description': 'Abs(Corr(Down)) vs Abs(Corr(Up))',
    'Mean A': round(abs(h9_down_corr), 4),
    'Mean B': round(abs(h9_up_corr), 4),
    'Effect Size': round(abs(h9_down_corr) - abs(h9_up_corr), 4),
    'T-stat': np.nan, 'p-value': np.nan,
    'Verdict': 'Confirmed' if abs(h9_down_corr) > abs(h9_up_corr) else 'Rejected'
})

# H10: Insider after Earnings Drop
df_hyp['h10_combo'] = ((df_hyp['insider_cluster_active'] == 1) & (df_hyp['consecutive_beats'] <= 0)).astype(int)
res = run_hypothesis_test(
    'H10 (Insider Earnings Drop)', 
    'Insider Buy + Missed Earnings',
    df_hyp[df_hyp['h10_combo'] == 1][target_col],
    df_hyp[df_hyp['h10_combo'] == 0][target_col],
    alternative='greater'
)
results.append(res)

# H11: Recurring Clusters
res = run_hypothesis_test(
    'H11 (Recurring Clusters)', 
    'cluster_count_60d > 1 vs == 1',
    df_hyp[df_hyp['cluster_count_60d'] > 1][target_col],
    df_hyp[df_hyp['cluster_count_60d'] == 1][target_col],
    alternative='greater'
)
results.append(res)

# H12: Persistent ARK
res = run_hypothesis_test(
    'H12 (Persistent ARK)', 
    'ark_conviction_streak > 3 vs <= 3',
    df_hyp[df_hyp['ark_conviction_streak'] > 3][target_col],
    df_hyp[df_hyp['ark_conviction_streak'] <= 3][target_col],
    alternative='greater'
)
results.append(res)

# H13: Multi-Source Convergence
source_cols = [
    'ark_conviction_score', 'insider_cluster_active', 'analyst_rating_score', 
    'politician_buy_count_60d_disclosure'
]
# Rough thresholding to count active sources
df_hyp['active_sources'] = (
    (df_hyp['ark_conviction_score'] > 0).astype(int) + 
    (df_hyp['insider_cluster_active'] == 1).astype(int) + 
    (df_hyp['analyst_rating_score'] > 3.5).astype(int) + 
    (df_hyp['politician_buy_count_60d_disclosure'] > 0).astype(int)
)
res = run_hypothesis_test(
    'H13 (Multi-Source)', 
    'Sources >= 2 vs Sources < 2',
    df_hyp[df_hyp['active_sources'] >= 2][target_col],
    df_hyp[df_hyp['active_sources'] < 2][target_col],
    alternative='greater'
)
results.append(res)

# Display Hypothesis Results
results_df = pd.DataFrame(results)
display(results_df)

# %% [markdown]
# ## 6. Sprint 10 Recommendations (Empfehlungen für Sprint 10)
# 
# Based on the empirical evidence, here are the recommendations for the final scoring model:
# 
# ### Final Feature Shortlist
# *Features demonstrating robust signal across importance metrics and hypothesis testing.*
# 1. **Insider**: `insider_cluster_score`, `cluster_count_60d` (stronger than single trades).
# 2. **ARK**: `ark_conviction_score`, `ark_weight_delta_20d`, `ark_multi_etf_signal`.
# 3. **Analyst**: `analyst_rating_score`, `analyst_net_sentiment_60d` (downgrades are particularly impactful).
# 4. **Technical**: `price_vs_sma200`, `rsi_14` (good for market regime context).
# 5. **Fundamentals**: `forward_pe`, `revenue_growth_yoy` (quality filters).
# 
# ### Features to Exclude or Downweight
# *Features with low permutation importance, zero LASSO coefficients, or low correlation.*
# - Short-term volume ratios (`volume_ratio_20d`).
# - Noisy 1-day weight deltas (`ark_weight_delta_1d`).
# - Politician data (due to severe sparsity/lag).
# 
# ### Rough Weight Recommendations (for scoring logic)
# - **ARK Signal**: 30%
# - **Insider Form4**: 35%
# - **Analyst/Earnings Catalyst**: 20%
# - **Fundamental/Technical context**: 15%
# 
# ### Open Questions
# - Interaction features (e.g., ARK buy + Insider buy) showed promise. How to best implement this in the score? Additive bonuses or multiplicative?
# - Should we build separate models for different market caps, given that analyst coverage and insider behavior differ wildly between mega-caps and micro-caps?
