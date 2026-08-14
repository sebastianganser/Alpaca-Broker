# %% [markdown]
# # Sprint 9: Exploratory Analysis - Feature-Return Correlations
# This notebook analyzes the predictive power of various features across different return horizons using Spearman rank correlation.

# %% [markdown]
# ## 1. Data Loading & Preparation / Daten laden & vorbereiten
# Load data, convert types, print overview, define feature groups and target columns.

# %%
import os
import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from scipy.stats import spearmanr
from sklearn.utils import resample
import warnings

warnings.filterwarnings('ignore')

# Set plotting style
sns.set_theme(style="whitegrid")
plt.rcParams['figure.figsize'] = (12, 8)

# Connect to database
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath('__file__')), '..', 'src'))
from trading_signals.config import get_settings
from sqlalchemy import create_engine

print("Connecting to database...")
settings = get_settings()
engine = create_engine(settings.database_url)

print("Loading data from signals.feature_snapshots...")
query = 'SELECT * FROM signals.feature_snapshots ORDER BY snapshot_date, ticker'
df = pd.read_sql(query, engine)

# Convert dates
if 'snapshot_date' in df.columns:
    df['snapshot_date'] = pd.to_datetime(df['snapshot_date'])

print(f"Loaded {len(df)} rows and {len(df.columns)} columns.")

# Define feature groups
feature_groups = {
    'ARK': ['ark_in_etf_count', 'ark_total_weight', 'ark_weight_delta_1d', 'ark_weight_delta_5d', 'ark_weight_delta_20d', 'ark_conviction_score', 'ark_multi_etf_signal', 'ark_increase_days_10d', 'ark_increase_days_20d', 'ark_conviction_streak', 'ark_weight_trend_20d'],
    'Insider': ['insider_net_buy_count_30d', 'insider_buy_value_30d', 'insider_cluster_active', 'insider_cluster_score', 'cluster_count_30d', 'cluster_count_60d', 'cluster_score_sum_60d', 'days_since_last_cluster'],
    'Analyst': ['analyst_rating_score', 'analyst_upgrades_30d', 'analyst_price_target_upside', 'analyst_downgrades_30d', 'analyst_net_sentiment_30d', 'analyst_net_sentiment_60d', 'analyst_upgrade_streak'],
    'Politician': ['politician_buy_count_60d_disclosure', 'politician_distinct_90d_disclosure', 'politician_buy_count_60d_transaction', 'politician_distinct_90d_transaction'],
    'Fundamentals': ['pe_ratio', 'forward_pe', 'ps_ratio', 'revenue_growth_yoy', 'profit_margin', 'debt_to_equity', 'pe_trend_4w', 'margin_trend_4w'],
    'Technical': ['price_vs_sma50', 'price_vs_sma200', 'rsi_14', 'relative_strength_spy', 'volume_ratio_20d', 'atr_14_pct'],
    'Earnings': ['earnings_days_until', 'consecutive_beats', 'surprise_trend_3q'],
    'Sentiment': ['sentiment_avg_7d', 'sentiment_avg_30d', 'sentiment_momentum', 'sentiment_neg_count_7d', 'sentiment_article_count_7d', 'market_sentiment_7d']
}

targets = ['return_1d', 'return_5d', 'return_20d']
exclude_cols = ['return_60d', 'form13f_top_holder_count', 'form13f_new_positions_count']

# Filter available features
all_features = [feat for group in feature_groups.values() for feat in group]
available_features = [f for f in all_features if f in df.columns and f not in exclude_cols]
available_targets = [t for t in targets if t in df.columns and t not in exclude_cols]

print(f"Available features for analysis: {len(available_features)}")
print(f"Available targets for analysis: {len(available_targets)}")

# %% [markdown]
# ## 2. Spearman Correlation Matrix (Feature -> Return) / Spearman Korrelationsmatrix
# Compute Spearman rank correlation for all features against return horizons.
# Includes p-values with Bonferroni correction for multiple testing.

# %%
print("--- Section 2: Spearman Correlation Matrix ---")

results = []
n_tests = len(available_features) * len(available_targets)
alpha_bonf = 0.05 / n_tests if n_tests > 0 else 0.05

for target in available_targets:
    for feature in available_features:
        # Drop missing values for the pair
        data_pair = df[[feature, target]].dropna()
        if len(data_pair) > 30:
            rho, pval = spearmanr(data_pair[feature], data_pair[target])
            results.append({
                'Feature': feature,
                'Target': target,
                'Spearman_Rho': rho,
                'P_Value': pval,
                'Significant': pval < alpha_bonf,
                'N_Obs': len(data_pair)
            })

corr_df = pd.DataFrame(results)

# Create a heatmap for the correlation matrix
if not corr_df.empty:
    pivot_corr = corr_df.pivot(index='Feature', columns='Target', values='Spearman_Rho')
    
    plt.figure(figsize=(14, 16))
    sns.heatmap(pivot_corr, cmap='RdBu', center=0, annot=True, fmt=".3f", 
                cbar_kws={'label': 'Spearman Rank Correlation'})
    plt.title("Spearman Correlation between Features and Return Horizons", fontsize=16)
    plt.tight_layout()
    plt.show()
else:
    print("Not enough data to compute correlations.")

# %% [markdown]
# ## 3. Top Features per Return Horizon / Top Features pro Horizont
# Rank features by absolute correlation for each horizon and plot the top 15.

# %%
print("--- Section 3: Top Features per Return Horizon ---")

top_n = 15
top_features_per_target = {}

for target in available_targets:
    target_df = corr_df[corr_df['Target'] == target].copy()
    target_df['Abs_Rho'] = target_df['Spearman_Rho'].abs()
    top_target = target_df.sort_values(by='Abs_Rho', ascending=False).head(top_n)
    top_features_per_target[target] = top_target['Feature'].tolist()
    
    plt.figure(figsize=(12, 8))
    
    colors = ['#2ecc71' if sig else '#95a5a6' for sig in top_target['Significant']]
    
    sns.barplot(x='Spearman_Rho', y='Feature', data=top_target, palette=colors)
    plt.title(f"Top {top_n} Features for {target} (Green = Significant after Bonferroni)", fontsize=14)
    plt.xlabel("Spearman Rank Correlation (ρ)")
    plt.ylabel("Feature")
    plt.tight_layout()
    plt.show()
    
    print(f"\nTop 10 features for {target} with Bootstrap Confidence Intervals (1000 iter):")
    # Bootstrap CI for top 10
    for feature in top_target['Feature'].head(10):
        data_pair = df[[feature, target]].dropna()
        n = len(data_pair)
        if n > 100:
            boot_rhos = []
            for _ in range(1000):
                boot_sample = resample(data_pair, n_samples=n)
                rho, _ = spearmanr(boot_sample[feature], boot_sample[target])
                boot_rhos.append(rho)
            ci_lower = np.percentile(boot_rhos, 2.5)
            ci_upper = np.percentile(boot_rhos, 97.5)
            orig_rho = top_target[top_target['Feature'] == feature]['Spearman_Rho'].values[0]
            print(f"  {feature}: ρ = {orig_rho:.4f} [95% CI: {ci_lower:.4f}, {ci_upper:.4f}]")

# %% [markdown]
# ## 4. Feature Group Analysis / Feature-Gruppen Analyse
# Average absolute correlation per feature group across return horizons.

# %%
print("--- Section 4: Feature Group Analysis ---")

# Map features to their groups
feature_to_group = {}
for group, features in feature_groups.items():
    for f in features:
        feature_to_group[f] = group

if not corr_df.empty:
    corr_df['Group'] = corr_df['Feature'].map(feature_to_group)
    corr_df['Abs_Rho'] = corr_df['Spearman_Rho'].abs()
    
    group_stats = corr_df.groupby(['Group', 'Target'])['Abs_Rho'].mean().reset_index()
    
    plt.figure(figsize=(14, 8))
    sns.barplot(x='Group', y='Abs_Rho', hue='Target', data=group_stats)
    plt.title("Average Absolute Spearman Correlation per Feature Group", fontsize=16)
    plt.xlabel("Feature Group")
    plt.ylabel("Mean |ρ|")
    plt.xticks(rotation=45)
    plt.legend(title='Return Horizon')
    plt.tight_layout()
    plt.show()
    
    print("Which signal source has the highest overall predictive power?")
    overall_group_rank = group_stats.groupby('Group')['Abs_Rho'].mean().sort_values(ascending=False)
    for i, (group, mean_rho) in enumerate(overall_group_rank.items(), 1):
        print(f"{i}. {group} (Mean |ρ| = {mean_rho:.4f})")

# %% [markdown]
# ## 5. Politician Dual-Date Evaluation (Hypothesis H7) / Politiker Dual-Datum Evaluation
# Compare _disclosure vs _transaction variants for Politician features.

# %%
print("--- Section 5: Politician Dual-Date Evaluation ---")

pol_features = [f for f in available_features if f.startswith('politician')]
pol_corr = corr_df[corr_df['Feature'].isin(pol_features)].copy()

if not pol_corr.empty:
    def extract_variant(f):
        if 'disclosure' in f: return 'Disclosure'
        if 'transaction' in f: return 'Transaction'
        return 'Unknown'
        
    def extract_base(f):
        return f.replace('_disclosure', '').replace('_transaction', '')
        
    pol_corr['Variant'] = pol_corr['Feature'].apply(extract_variant)
    pol_corr['Base_Feature'] = pol_corr['Feature'].apply(extract_base)
    
    plt.figure(figsize=(12, 6))
    sns.barplot(x='Base_Feature', y='Spearman_Rho', hue='Variant', data=pol_corr[pol_corr['Target'] == 'return_20d'])
    plt.title("Politician Features: Disclosure vs Transaction Date (Target: return_20d)", fontsize=14)
    plt.ylabel("Spearman Rank Correlation (ρ)")
    plt.xticks(rotation=15)
    plt.tight_layout()
    plt.show()
    
    print("Comparing Disclosure vs Transaction variants across all targets:")
    for base in pol_corr['Base_Feature'].unique():
        for target in available_targets:
            sub = pol_corr[(pol_corr['Base_Feature'] == base) & (pol_corr['Target'] == target)]
            if len(sub) == 2:
                disc_val = sub[sub['Variant'] == 'Disclosure']['Spearman_Rho'].values[0]
                trans_val = sub[sub['Variant'] == 'Transaction']['Spearman_Rho'].values[0]
                winner = 'Disclosure' if abs(disc_val) > abs(trans_val) else 'Transaction'
                print(f"  {base} ({target}): Disclosure = {disc_val:.4f}, Transaction = {trans_val:.4f} -> Winner: {winner}")

# %% [markdown]
# ## 6. Quintile Analysis for Top Features / Quintils-Analyse für Top Features
# Split top 5 features (by |ρ| with return_20d) into quintiles and analyze mean return.

# %%
print("--- Section 6: Quintile Analysis ---")

if 'return_20d' in top_features_per_target:
    top_5_features = top_features_per_target['return_20d'][:5]
    
    fig, axes = plt.subplots(len(top_5_features), 1, figsize=(10, 4 * len(top_5_features)))
    if len(top_5_features) == 1:
        axes = [axes]
        
    for i, feature in enumerate(top_5_features):
        plot_data = df[[feature, 'return_20d']].dropna().copy()
        
        if len(plot_data) > 50:
            try:
                # Using rank(method='first') to ensure bins are unique in case of many duplicate values
                plot_data['Quintile'] = pd.qcut(plot_data[feature].rank(method='first'), q=5, labels=['Q1(Low)', 'Q2', 'Q3', 'Q4', 'Q5(High)'])
                
                quintile_means = plot_data.groupby('Quintile', observed=True)['return_20d'].mean().reset_index()
                
                sns.barplot(x='Quintile', y='return_20d', data=quintile_means, ax=axes[i], color='steelblue')
                axes[i].set_title(f"Mean 20d Return by {feature} Quintiles", fontsize=12)
                axes[i].set_ylabel("Mean return_20d")
            except Exception as e:
                axes[i].text(0.5, 0.5, f"Could not compute quintiles for {feature}", ha='center')
    
    plt.tight_layout()
    plt.show()

# %% [markdown]
# ## 7. Temporal Stability / Zeitliche Stabilität
# Compute Spearman ρ separately per month for top features to evaluate stability over time.

# %%
print("--- Section 7: Temporal Stability ---")

if 'return_20d' in top_features_per_target and 'snapshot_date' in df.columns:
    df['YearMonth'] = df['snapshot_date'].dt.to_period('M')
    top_3_features = top_features_per_target['return_20d'][:3]
    
    monthly_corrs = []
    for month, group in df.groupby('YearMonth'):
        if len(group) > 30:
            for feature in top_3_features:
                valid_data = group[[feature, 'return_20d']].dropna()
                if len(valid_data) > 10:
                    rho, _ = spearmanr(valid_data[feature], valid_data['return_20d'])
                    monthly_corrs.append({'YearMonth': str(month), 'Feature': feature, 'Spearman_Rho': rho})
                    
    if monthly_corrs:
        monthly_corr_df = pd.DataFrame(monthly_corrs)
        
        plt.figure(figsize=(14, 6))
        sns.lineplot(x='YearMonth', y='Spearman_Rho', hue='Feature', data=monthly_corr_df, marker='o')
        plt.title("Monthly Spearman Correlation (Feature vs return_20d)", fontsize=14)
        plt.xlabel("Month")
        plt.ylabel("Spearman Rank Correlation (ρ)")
        plt.xticks(rotation=45)
        plt.axhline(0, color='black', linestyle='--')
        plt.tight_layout()
        plt.show()

# %% [markdown]
# ## 8. Inter-Feature Correlations (Multicollinearity) / Inter-Feature Korrelationen
# Feature x Feature Spearman correlation matrix to identify redundant features.

# %%
print("--- Section 8: Inter-Feature Correlations ---")

feat_df = df[available_features]
print("Computing pairwise Spearman correlation matrix (this may take a moment)...")
feat_corr_matrix = feat_df.corr(method='spearman')

cluster_grid = sns.clustermap(feat_corr_matrix.fillna(0), cmap='coolwarm', center=0, figsize=(16, 16),
               dendrogram_ratio=0.15, cbar_pos=(0.02, 0.8, 0.03, 0.18))
cluster_grid.fig.suptitle("Inter-Feature Spearman Correlation (Hierarchical Clustering)", fontsize=16, y=1.02)
plt.show()

print("\nHighly Correlated Feature Pairs (|ρ| > 0.70):")
# Extract upper triangle without diagonal
upper_tri = feat_corr_matrix.where(np.triu(np.ones(feat_corr_matrix.shape), k=1).astype(bool))
high_corr = []
for col in upper_tri.columns:
    for row in upper_tri.index:
        val = upper_tri.loc[row, col]
        if pd.notna(val) and abs(val) > 0.7:
            high_corr.append({'Feature_1': row, 'Feature_2': col, 'Correlation': val})

if high_corr:
    high_corr_df = pd.DataFrame(high_corr).sort_values(by='Correlation', key=abs, ascending=False)
    for _, r in high_corr_df.iterrows():
        print(f"  {r['Feature_1']} <-> {r['Feature_2']}: {r['Correlation']:.4f}")
    
    print("\nRecommendation for feature reduction:")
    print("Consider removing one feature from each highly correlated pair or using PCA within groups.")
else:
    print("No feature pairs with |ρ| > 0.70 found.")
