# %% [markdown]
# # Sprint 9: 01 - Deskriptive Statistik
# Exploratory Data Analysis (EDA) of feature snapshots.

# %%
import os, sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# Setup plotting style
sns.set_theme(style="whitegrid")
plt.rcParams['figure.figsize'] = (10, 6)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath('__file__')), '..', 'src'))
from trading_signals.config import get_settings
from sqlalchemy import create_engine
from IPython.display import display

# %% [markdown]
# ## 1. Daten Laden & Überblick (Data Loading & Overview)
# Load all feature_snapshots data from the database and provide a high-level summary.

# %%
settings = get_settings()
engine = create_engine(settings.database_url)
print("Loading data from database...")
df = pd.read_sql('SELECT * FROM signals.feature_snapshots ORDER BY snapshot_date, ticker', engine)
print("Data loaded successfully.")

# Feature Groups Definition
feature_groups = {
    'ARK': ['ark_in_etf_count', 'ark_total_weight', 'ark_weight_delta_1d', 'ark_weight_delta_5d', 'ark_weight_delta_20d', 'ark_conviction_score', 'ark_multi_etf_signal', 'ark_increase_days_10d', 'ark_increase_days_20d', 'ark_conviction_streak', 'ark_weight_trend_20d'],
    'Insider': ['insider_net_buy_count_30d', 'insider_buy_value_30d', 'insider_cluster_active', 'insider_cluster_score', 'cluster_count_30d', 'cluster_count_60d', 'cluster_score_sum_60d', 'days_since_last_cluster'],
    'Analyst': ['analyst_rating_score', 'analyst_upgrades_30d', 'analyst_price_target_upside', 'analyst_downgrades_30d', 'analyst_net_sentiment_30d', 'analyst_net_sentiment_60d', 'analyst_upgrade_streak'],
    'Politician': ['politician_buy_count_60d_disclosure', 'politician_distinct_90d_disclosure', 'politician_buy_count_60d_transaction', 'politician_distinct_90d_transaction'],
    'Fundamentals': ['pe_ratio', 'forward_pe', 'ps_ratio', 'revenue_growth_yoy', 'profit_margin', 'debt_to_equity', 'pe_trend_4w', 'margin_trend_4w'],
    'Technical': ['price_vs_sma50', 'price_vs_sma200', 'rsi_14', 'relative_strength_spy', 'volume_ratio_20d', 'atr_14_pct'],
    'Earnings': ['earnings_days_until', 'consecutive_beats', 'surprise_trend_3q'],
    'Sentiment': ['sentiment_avg_7d', 'sentiment_avg_30d', 'sentiment_momentum', 'sentiment_neg_count_7d', 'sentiment_article_count_7d', 'market_sentiment_7d'],
}

targets = ['return_1d', 'return_5d', 'return_20d']
# Excluding return_60d and 13F features

# Convert snapshot_date to datetime if not already
if not pd.api.types.is_datetime64_any_dtype(df['snapshot_date']):
    df['snapshot_date'] = pd.to_datetime(df['snapshot_date'])

print(f"Dataset Shape: {df.shape}")
print(f"Date Range: {df['snapshot_date'].min().date()} to {df['snapshot_date'].max().date()}")
print(f"Unique Tickers: {df['ticker'].nunique()}")
print(f"Total Snapshots: {len(df)}")
print("\n--- DataFrame Info ---")
df.info()

# %% [markdown]
# ## 2. Analyse der Fehlwerte (Missing Rate Analysis)
# Analyze the missing rates across different features and over time.

# %%
missing_rates = df.isnull().mean().sort_values(ascending=False) * 100
missing_rates = missing_rates[missing_rates > 0]

if not missing_rates.empty:
    plt.figure(figsize=(12, 14))
    sns.barplot(x=missing_rates.values, y=missing_rates.index, hue=missing_rates.index, palette="viridis", legend=False)
    plt.title("Missing Rates per Feature (%)")
    plt.xlabel("Missing Rate (%)")
    plt.ylabel("Feature")
    plt.tight_layout()
    plt.show()
else:
    print("No missing values found in the dataset.")

# %%
# Missing rate per feature x month heatmap
df['month'] = df['snapshot_date'].dt.to_period('M')
# Drop non-feature columns before grouping
feat_cols = [c for c in df.columns if c not in ['month', 'snapshot_date', 'ticker', 'id']]
if feat_cols:
    missing_by_month = df.groupby('month')[feat_cols].apply(lambda x: x.isnull().mean()) * 100
    
    plt.figure(figsize=(20, 10))
    sns.heatmap(missing_by_month.T, cmap="YlOrRd", cbar_kws={'label': 'Missing Rate (%)'})
    plt.title("Missing Rate Heatmap: Feature vs. Month")
    plt.xlabel("Month")
    plt.ylabel("Feature")
    plt.tight_layout()
    plt.show()

# %%
# Feature activation curves (>50% fill rate)
activation_dates = {}
if feat_cols:
    for group_name, features in feature_groups.items():
        available_features = [f for f in features if f in df.columns]
        if not available_features:
            continue
        # Calculate fill rate per month for the group average
        fill_rate = 100 - missing_by_month[available_features].mean(axis=1)
        # Find first month with > 50% fill rate
        active_months = fill_rate[fill_rate > 50]
        if not active_months.empty:
            activation_dates[group_name] = active_months.index[0]

print("Feature Group Activation Dates (>50% Fill Rate):")
for group, date in activation_dates.items():
    print(f"- {group}: {date}")

# %% [markdown]
# ## 3. Univariate Statistiken (Univariate Statistics)
# Describe numeric features, show distributions and detect outliers.

# %%
numeric_cols = df.select_dtypes(include=[np.number]).columns
desc_stats = df[numeric_cols].describe().T
print("Univariate Statistics (Summary):")
display(desc_stats)

# %%
# Distribution histograms per group
for group_name, features in feature_groups.items():
    available_features = [f for f in features if f in df.columns]
    if not available_features:
        continue
    
    num_features = len(available_features)
    cols = 3
    rows = int(np.ceil(num_features / cols))
    
    fig, axes = plt.subplots(rows, cols, figsize=(15, 4 * rows))
    fig.suptitle(f"Distributions: {group_name} Features", fontsize=16)
    
    # Handle scalar axes case
    if rows == 1 and cols == 1:
        axes = np.array([axes])
    axes = axes.flatten()
    
    for i, feature in enumerate(available_features):
        data = df[feature].dropna()
        if not data.empty:
            sns.histplot(data, bins=30, ax=axes[i], kde=True)
        axes[i].set_title(feature)
        axes[i].set_xlabel('')
        axes[i].set_ylabel('')
        
    for j in range(i + 1, len(axes)):
        fig.delaxes(axes[j])
        
    plt.tight_layout()
    plt.show()

# %%
# Outlier detection (IQR-based, report >5%)
outlier_report = {}
for col in numeric_cols:
    if col in ['id', 'ticker']:
        continue
    Q1 = df[col].quantile(0.25)
    Q3 = df[col].quantile(0.75)
    IQR = Q3 - Q1
    lower_bound = Q1 - 1.5 * IQR
    upper_bound = Q3 + 1.5 * IQR
    
    non_null_count = df[col].notnull().sum()
    if non_null_count > 0:
        outliers = ((df[col] < lower_bound) | (df[col] > upper_bound)).sum()
        outlier_pct = (outliers / non_null_count) * 100
        if outlier_pct > 5:
            outlier_report[col] = outlier_pct

print("Features with >5% outliers (IQR method):")
for feature, pct in sorted(outlier_report.items(), key=lambda x: x[1], reverse=True):
    print(f"- {feature}: {pct:.2f}% outliers")

# %% [markdown]
# ## 4. Zielvariablen-Analyse (Target Return Analysis)
# Analyze target returns: return_1d, return_5d, return_20d.

# %%
# Distribution plots for returns
available_targets = [t for t in targets if t in df.columns]
if available_targets:
    fig, axes = plt.subplots(1, len(available_targets), figsize=(6 * len(available_targets), 5))
    fig.suptitle("Target Return Distributions", fontsize=16)
    
    if len(available_targets) == 1:
        axes = [axes]

    for i, target in enumerate(available_targets):
        data = df[target].dropna()
        if not data.empty:
            sns.histplot(data, bins=50, ax=axes[i], kde=True)
        axes[i].set_title(target)
        axes[i].set_xlabel('Return')
        axes[i].set_ylabel('Frequency')

    plt.tight_layout()
    plt.show()

# %%
# Summary statistics for returns
print("Return Horizon Statistics:")
if available_targets:
    display(df[available_targets].describe().T)

# %%
# Return Autocorrelation
print("Return Autocorrelation (Are returns independent?):")
if available_targets:
    for target in available_targets:
        # Sort by ticker and date, then compute lag 1 correlation
        df_sorted = df.sort_values(by=['ticker', 'snapshot_date'])
        df_sorted[f'{target}_lag1'] = df_sorted.groupby('ticker')[target].shift(1)
        # Using Spearman correlation as requested
        corr = df_sorted[[target, f'{target}_lag1']].corr(method='spearman').iloc[0, 1]
        print(f"- {target} Lag-1 Spearman Correlation: {corr:.4f}")

# %%
# Returns by month
if available_targets:
    df['calendar_month'] = df['snapshot_date'].dt.month
    fig, axes = plt.subplots(1, len(available_targets), figsize=(6 * len(available_targets), 6))
    fig.suptitle("Returns by Calendar Month", fontsize=16)
    
    if len(available_targets) == 1:
        axes = [axes]
    
    for i, target in enumerate(available_targets):
        sns.boxplot(x='calendar_month', y=target, data=df, ax=axes[i], palette="Set3", showfliers=False, hue='calendar_month', legend=False)
        axes[i].set_title(target)
        axes[i].set_xlabel('Month')
        axes[i].set_ylabel('Return')
        
    plt.tight_layout()
    plt.show()

# %% [markdown]
# ## 5. Feature-Gruppen Zusammenfassung (Feature Group Summaries)
# Detailed summaries for each feature group.

# %%
for group_name, features in feature_groups.items():
    available = [f for f in features if f in df.columns]
    if not available:
        continue
    
    print(f"\n{'='*50}\nGroup: {group_name}\n{'='*50}")
    
    for feature in available:
        # Check if feature is boolean or pseudo-boolean
        is_bool = False
        non_null_vals = df[feature].dropna()
        if not non_null_vals.empty and set(non_null_vals.unique()).issubset({0, 1, 0.0, 1.0}):
            is_bool = True
            
        coverage = df[feature].notnull().mean() * 100
        print(f"\nFeature: {feature}")
        print(f"Coverage: {coverage:.2f}%")
        
        if is_bool:
            counts = df[feature].value_counts(normalize=True) * 100
            print(f"Boolean Frequencies:\n{counts.to_string()}")
        else:
            desc = df[feature].describe()
            print(f"Mean: {desc['mean']:.4f} | Std: {desc['std']:.4f} | Min: {desc['min']:.4f} | Max: {desc['max']:.4f}")
            
            # Top 5 highest values
            top_5 = df.nlargest(5, feature)[['ticker', 'snapshot_date', feature]]
            print(f"Top 5 Tickers by value:\n{top_5.to_string(index=False)}")
