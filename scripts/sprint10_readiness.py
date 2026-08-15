"""
Sprint 10 Readiness Check
=========================
Evaluates whether the data is mature enough to build a
reliable Signal Scoring model (Sprint 10).

Run locally:  DATABASE_URL=postgresql://... python notebooks/sprint10_readiness.py
Run in container:  python -m trading_signals.analysis.sprint10_readiness
"""

# %% ── Setup ────────────────────────────────────────────────────────────
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pandas as pd
import numpy as np
from scipy import stats
from sqlalchemy import create_engine, text

from trading_signals.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url)

# %% ── Thresholds ───────────────────────────────────────────────────────
# These are the minimum requirements for Sprint 10

THRESHOLDS = {
    "min_distinct_dates": 120,       # ~6 months of trading days
    "min_snapshots": 80_000,         # total rows
    "min_tickers": 600,              # active universe
    "min_return_20d_pct": 65.0,      # % of rows with return_20d filled
    "min_return_60d_pct": 35.0,      # % of rows with return_60d filled
    "min_earnings_quarters": 2,      # distinct earnings quarters covered
    "min_analysis_reports": 3,       # monthly reports for stability check
    "max_correlation_drift": 0.5,    # max Kendall τ change in top-10 ranking
    "min_regime_diversity": 0.02,    # min std of monthly SPY returns (not monotonic)
}

# %% ── Load Data ────────────────────────────────────────────────────────

print("=" * 72)
print("  SPRINT 10 READINESS CHECK")
print(f"  Date: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
print("=" * 72)

df = pd.read_sql(
    text("SELECT * FROM signals.feature_snapshots ORDER BY snapshot_date"),
    engine,
)
df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])

# Load analysis reports for stability check
reports_df = pd.read_sql(
    text("SELECT report_date, feature_correlations, consensus_features FROM signals.analysis_reports ORDER BY report_date"),
    engine,
)

# Load SPY prices for regime diversity
spy_prices = pd.read_sql(
    text("""
        SELECT trade_date, close
        FROM signals.prices_daily
        WHERE ticker = 'SPY'
        ORDER BY trade_date
    """),
    engine,
)

results = []

# %% ── Check 1: Data Volume ────────────────────────────────────────────

print("\n" + "─" * 72)
print("  1. DATA VOLUME")
print("─" * 72)

distinct_dates = df["snapshot_date"].nunique()
total_snapshots = len(df)
distinct_tickers = df["ticker"].nunique()
date_min = df["snapshot_date"].min().strftime("%Y-%m-%d")
date_max = df["snapshot_date"].max().strftime("%Y-%m-%d")

ok_dates = distinct_dates >= THRESHOLDS["min_distinct_dates"]
ok_snaps = total_snapshots >= THRESHOLDS["min_snapshots"]
ok_tickers = distinct_tickers >= THRESHOLDS["min_tickers"]

print(f"  Distinct dates:    {distinct_dates:>6}  (min: {THRESHOLDS['min_distinct_dates']})  {'✅' if ok_dates else '❌'}")
print(f"  Total snapshots:  {total_snapshots:>7}  (min: {THRESHOLDS['min_snapshots']:,})  {'✅' if ok_snaps else '❌'}")
print(f"  Distinct tickers:  {distinct_tickers:>6}  (min: {THRESHOLDS['min_tickers']})  {'✅' if ok_tickers else '❌'}")
print(f"  Date range:        {date_min} → {date_max}")

results.extend([ok_dates, ok_snaps, ok_tickers])

# %% ── Check 2: Target Return Coverage ─────────────────────────────────

print("\n" + "─" * 72)
print("  2. TARGET RETURN COVERAGE")
print("─" * 72)

for col, min_pct in [
    ("return_1d", 90.0),
    ("return_5d", 85.0),
    ("return_20d", THRESHOLDS["min_return_20d_pct"]),
    ("return_60d", THRESHOLDS["min_return_60d_pct"]),
]:
    if col in df.columns:
        filled = df[col].notna().sum()
        pct = 100.0 * filled / total_snapshots
        ok = pct >= min_pct
        print(f"  {col:>12}:  {filled:>7} / {total_snapshots}  ({pct:5.1f}%)  (min: {min_pct}%)  {'✅' if ok else '❌'}")
        results.append(ok)
    else:
        print(f"  {col:>12}:  COLUMN MISSING  ❌")
        results.append(False)

# %% ── Check 3: Feature Coverage ───────────────────────────────────────

print("\n" + "─" * 72)
print("  3. FEATURE GROUP COVERAGE (latest date)")
print("─" * 72)

FEATURE_GROUPS = {
    "ARK": ["ark_in_etf_count", "ark_total_weight", "ark_conviction_score",
            "ark_multi_etf_signal", "ark_conviction_streak"],
    "Insider": ["insider_cluster_score", "insider_cluster_active",
                "cluster_count_30d", "days_since_last_cluster"],
    "Analyst": ["analyst_rating_score", "analyst_price_target_upside",
                "analyst_net_sentiment_30d"],
    "Politician": ["politician_buy_count_60d_disclosure",
                   "politician_buy_count_60d_transaction"],
    "Fundamentals": ["pe_ratio", "forward_pe", "profit_margin", "debt_to_equity"],
    "Technical": ["price_vs_sma50", "rsi_14", "relative_strength_spy"],
    "Earnings": ["earnings_days_until", "consecutive_beats"],
    "Sentiment": ["sentiment_avg_7d", "sentiment_avg_30d", "sentiment_momentum"],
}

latest_date = df["snapshot_date"].max()
latest = df[df["snapshot_date"] == latest_date]

for group, cols in FEATURE_GROUPS.items():
    available = [c for c in cols if c in latest.columns]
    if available:
        fill_pct = 100.0 * latest[available].notna().mean().mean()
    else:
        fill_pct = 0.0
    ok = fill_pct >= 50.0
    print(f"  {group:>14}:  {fill_pct:5.1f}%  {'✅' if ok else '❌'}")
    results.append(ok)

# %% ── Check 4: Earnings Cycle Coverage ────────────────────────────────

print("\n" + "─" * 72)
print("  4. EARNINGS CYCLE COVERAGE")
print("─" * 72)

earnings_df = pd.read_sql(
    text("""
        SELECT DISTINCT
            EXTRACT(YEAR FROM earnings_date) AS yr,
            EXTRACT(QUARTER FROM earnings_date) AS qtr
        FROM signals.earnings_calendar
        WHERE earnings_date BETWEEN :start AND :end
    """),
    engine,
    params={"start": date_min, "end": date_max},
)
n_quarters = len(earnings_df)
ok_quarters = n_quarters >= THRESHOLDS["min_earnings_quarters"]
print(f"  Distinct earnings quarters: {n_quarters}  (min: {THRESHOLDS['min_earnings_quarters']})  {'✅' if ok_quarters else '❌'}")
if not earnings_df.empty:
    for _, row in earnings_df.iterrows():
        print(f"    → Q{int(row['qtr'])} {int(row['yr'])}")
results.append(ok_quarters)

# %% ── Check 5: Market Regime Diversity ────────────────────────────────

print("\n" + "─" * 72)
print("  5. MARKET REGIME DIVERSITY (SPY monthly returns)")
print("─" * 72)

if not spy_prices.empty:
    spy_prices["trade_date"] = pd.to_datetime(spy_prices["trade_date"])
    spy_monthly = spy_prices.set_index("trade_date")["close"].resample("ME").last()
    spy_returns = spy_monthly.pct_change().dropna()

    ret_std = spy_returns.std()
    ret_range = spy_returns.max() - spy_returns.min()
    n_positive = (spy_returns > 0).sum()
    n_negative = (spy_returns < 0).sum()

    ok_regime = ret_std >= THRESHOLDS["min_regime_diversity"]

    print(f"  Monthly return std:   {ret_std:.4f}  (min: {THRESHOLDS['min_regime_diversity']})  {'✅' if ok_regime else '❌'}")
    print(f"  Monthly return range: {ret_range:.4f}")
    print(f"  Positive months: {n_positive}  |  Negative months: {n_negative}")

    if n_negative == 0:
        print("  ⚠️  Kein einziger negativer Monat → Modell kennt nur Bull-Regime")
        ok_regime = False

    for idx, val in spy_returns.items():
        direction = "📈" if val > 0 else "📉"
        print(f"    {idx.strftime('%Y-%m')}: {val:+.2%}  {direction}")
else:
    print("  ❌ Keine SPY-Preisdaten gefunden")
    ok_regime = False

results.append(ok_regime)

# %% ── Check 6: Correlation Stability (across monthly reports) ─────────

print("\n" + "─" * 72)
print("  6. CORRELATION STABILITY (across monthly reports)")
print("─" * 72)

n_reports = len(reports_df)
ok_reports = n_reports >= THRESHOLDS["min_analysis_reports"]
print(f"  Analysis reports available: {n_reports}  (min: {THRESHOLDS['min_analysis_reports']})  {'✅' if ok_reports else '❌'}")

ok_stability = False
if n_reports >= 2:
    # Compare top-10 feature rankings between first and last report
    try:
        first_consensus = reports_df.iloc[0]["consensus_features"]
        last_consensus = reports_df.iloc[-1]["consensus_features"]

        if first_consensus and last_consensus:
            first_top10 = {f["feature"]: i for i, f in enumerate(first_consensus[:10])}
            last_top10 = {f["feature"]: i for i, f in enumerate(last_consensus[:10])}

            # Features in common
            common = set(first_top10.keys()) & set(last_top10.keys())
            overlap_pct = 100.0 * len(common) / 10

            print(f"  Top-10 overlap (first vs last report): {len(common)}/10 ({overlap_pct:.0f}%)")
            if common:
                print(f"  Stable features: {', '.join(sorted(common))}")

            ok_stability = overlap_pct >= 50.0
            print(f"  Ranking stability: {'✅' if ok_stability else '❌'} (min 50% overlap)")
        else:
            print("  ⚠️  Consensus-Daten nicht verfügbar in Reports")
    except Exception as e:
        print(f"  ⚠️  Stability check failed: {e}")
elif n_reports == 1:
    print("  ⚠️  Nur 1 Report → Stabilität nicht prüfbar. Warte auf weitere Monate.")
else:
    print("  ⚠️  Keine Reports vorhanden → erst Sprint 9 Analysis triggern")

results.append(ok_reports)
results.append(ok_stability)

# %% ── Check 7: Minimum rows for ML training ──────────────────────────

print("\n" + "─" * 72)
print("  7. ML TRAINING DATA SUFFICIENCY")
print("─" * 72)

if "return_20d" in df.columns:
    ml_rows = df["return_20d"].notna().sum()
    # For train/test split 70/30: need at least 5000 for meaningful training
    ok_ml = ml_rows >= 30_000
    print(f"  Rows with return_20d (ML target): {ml_rows:,}  (min: 30,000)  {'✅' if ok_ml else '❌'}")

    # Check chronological split quality
    dates_with_target = df[df["return_20d"].notna()]["snapshot_date"].nunique()
    train_dates = int(dates_with_target * 0.7)
    test_dates = dates_with_target - train_dates
    print(f"  Train dates: {train_dates}  |  Test dates: {test_dates}")
    print(f"  Train/test date ratio: {train_dates}/{test_dates} = {train_dates/max(test_dates,1):.1f}")

    ok_test = test_dates >= 20
    print(f"  Test set sufficient (≥20 dates): {'✅' if ok_test else '❌'}")
    results.append(ok_ml)
    results.append(ok_test)
else:
    print("  ❌ return_20d column missing")
    results.extend([False, False])

# %% ── VERDICT ──────────────────────────────────────────────────────────

print("\n" + "=" * 72)
passed = sum(results)
total = len(results)
pct = 100.0 * passed / total

if pct >= 90:
    verdict = "🟢 GO — Sprint 10 kann starten!"
    color = "green"
elif pct >= 60:
    verdict = "🟡 BEDINGT — Sprint 10 möglich, aber einige Checks fehlen"
    color = "yellow"
else:
    verdict = "🔴 WARTEN — Datenstand noch nicht ausreichend"
    color = "red"

print(f"  VERDICT:  {passed}/{total} Checks bestanden ({pct:.0f}%)")
print(f"  {verdict}")
print("=" * 72)

# Estimate when ready
if not ok_dates:
    days_needed = THRESHOLDS["min_distinct_dates"] - distinct_dates
    # ~5 trading days per week
    weeks_needed = days_needed / 5
    est_date = pd.Timestamp.now() + pd.Timedelta(weeks=weeks_needed)
    print(f"\n  📅 Geschätzte Bereitschaft: ~{est_date.strftime('%Y-%m-%d')}")
    print(f"     ({days_needed} weitere Handelstage nötig, ~{weeks_needed:.0f} Wochen)")

print()
