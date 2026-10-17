# LEARNINGS_HYPOTHESES.md – Hypotheses & Planned Investigations

> Forward-looking research agenda: hypotheses to test and investigations to run once sufficient data has been collected.
>
> See also: [INDEX.md](INDEX.md) · [LEARNINGS.md](LEARNINGS.md)

**Last updated:** October 2026 (H14 pre-registered)

---

## Planned Investigations

### Phase 1 – After 1 Month of Data Collection

- [ ] **Data Quality:** How many trading days do we have without gaps? How often have sources failed?
- [ ] **Universe Growth:** How fast does the ticker universe grow? Which sources add the most tickers?
- [ ] **ARK Activity:** How often do ARK holdings change? Are most deltas rebalances or real conviction?
- [ ] **Insider Frequency:** How many Form 4 filings per day? Which companies have the most insider trades?

### Phase 2 – After 3 Months of Data Collection

- [ ] **Feature ↔ Return Correlations:** Which features correlate with 1/5/20-day returns?
- [ ] **Cluster Analysis:** How often do insider clusters occur? How do they correlate with price movements?
- [ ] **ARK Predictive Power:** When ARK adds to a position, how does the ticker perform over the next 20 days?
- [ ] **Politician Delay:** Is the signal really as delayed as feared?
- [ ] **Multi-Signal Overlaps:** How often do two or three signal sources agree on the same ticker?

### Phase 3 – After 6 Months of Data Collection

- [ ] **Feature Importance:** Which features are most important according to Random Forest?
- [ ] **Optimal Scoring:** Which weightings deliver the best backtests?
- [ ] **Sector Differences:** Do signals work equally well across all sectors?
- [ ] **Temporal Stability:** Are signals stable across market phases (bull/bear)?
- [ ] **False Positive Rate:** How many "good scores" led to losses?

---

## Hypotheses (to be tested)

### H1: ARK Buying in Multiple ETFs Simultaneously is a Strong Signal
When Cathie Wood's team adds to the same ticker across multiple ARK ETFs, it's more likely conviction than rebalancing.

### H2: Insider Clusters Beat Individual Insider Buys
When multiple insiders of a company buy within a few days, it's more predictive than a single large purchase.

### H3: Form 4 Combined with ARK is Stronger Than Either Alone
Signals from different sources should be independent and mutually reinforcing.

### H4: Weight Changes in ARK are More Informative Than Absolute Shares
Because share changes can be driven by in-/outflows, weight changes are the purer signal.

### H5: ARK Sells are Less Predictive Than ARK Buys
Sells can have many reasons (rebalancing, outflows), buys are more targeted.

### H6: Small-Cap Tickers React More Strongly to ARK Trades Than Large-Caps
Market impact effect: ARK moves prices in small caps with its volume.

### H7: Politician Trades are Too Delayed for Alpha
But perhaps useful as a feature in combination with other signals.

### H8: Technical Indicators Alone Don't Generate Alpha Signals
But in combination with fundamentals, they could contribute.

### H9: Analyst Downgrades are Stronger Signals Than Upgrades
Because banks rarely write negatively about their clients – when they do, it's serious.

### H10: Insider Cluster Buys After an Earnings Drop are a Contrarian Signal
Insiders buy when the market has overreacted.

### H11: Recurring Insider Clusters are Stronger Than Single Clusters
Two consecutive clusters for the same ticker within 60 days suggest sustained insider conviction. A single cluster could be coincidental timing; repeated clusters imply systematic buying. **Observed:** TSM had Score 1591 (2026-03-03) followed by Score 718 (2026-04-03) — this sustained pattern should be more predictive than either cluster alone.

### H12: Persistent ARK Conviction Over Multiple Days is a Stronger Signal
ARK increasing a position across multiple consecutive snapshots (not just one day) suggests genuine conviction rather than a one-day rebalance. A ticker that's been `increased` for 5 out of the last 10 trading days is a fundamentally different signal than one increased on a single day.

### H13: Converging Multi-Source Signals Over a Time Window Beat Point-in-Time Overlaps
When insider buying, ARK accumulation, and analyst upgrades all occur for the same ticker within a 30-day window (but not necessarily on the same day), the convergence is a very strong signal. This is different from checking "do they overlap today?" — the temporal window matters.

### H14: High Days-to-Cover Predicts Lower Returns (pre-registered 2026-10-17)
**Status:** 🟡 Pre-registered, collecting out-of-sample data. Expected verdict: report of 2027-12-01 (≈ December 2027).

**Hypothesis:** Stocks with higher FINRA days-to-cover (short interest / average daily volume) have **lower** subsequent returns, i.e. the cross-sectional rank IC of `short_interest_days_to_cover` is negative.

**Motivation:** The first evaluation of the FINRA short interest (monthly `feature_analysis` of 2026-10-17, in-sample window 2022-07-28 … 2026-10-16) showed a consistently negative daily rank IC at every horizon: `return_5d` rho −0.016 (t_NW −3.32), `return_20d` rho −0.029 (ICIR −0.35, t_NW −3.20, 1,040 dates), `return_60d` rho −0.044 (t_NW −3.40), `return_barrier_14d` t_NW −1.85. The direction matches the literature, but the result does **not** survive the Bonferroni correction (~435 tests) and was selected from many tested features. Because all data up to 2026-10-16 has been looked at, a confirmatory test may only use data that did not exist at the time of the look.

**Pre-registered design (frozen – any change means H14 must be re-registered):**
- **Feature:** `short_interest_days_to_cover` exactly as defined by `FEATURE_VERSION` 2026.10-3 (`derived/short_interest_features.py`: settlement date ≤ the 9th NYSE session before `d`, newest usable settlement at most 40 days old, 999.99 or zero volume → NULL). If the feature definition changes, H14 must be re-registered (`H14_FEATURE_VERSION` in `analysis/feature_report.py`, guarded by a unit test).
- **Decision target:** `return_20d` = close(d+20) / open(d+1) − 1 as excess return per `prepare_frame` (rank IC is unaffected by the cross-sectional demeaning). **Secondary, information only, never decision-relevant:** `return_barrier_14d` (same dates).
- **Sample:** snapshot dates ≥ **2026-10-19** (first NYSE session after the in-sample look), point-in-time universe of `feature_snapshots`, only dates with ≥ 10 non-null (feature, target) pairs (`MIN_OBS_PER_DATE`).
- **Statistic:** daily Spearman rank IC (`stats.daily_rank_ic`), mean over the dates, Newey-West t with lag `nw_lag_for_horizon(20)` = 19 (`stats.mean_test`), **one-sided**: p = Φ(t_NW) (normal CDF) against H1: mean IC < 0.
- **Fixed sample size, single look:** the decisive test uses exactly the **first 250 out-of-sample dates with a valid daily IC for `return_20d`** (≈ one trading year, 2026-10-19 … ≈ mid-October 2027; the 20d targets of the last dates mature ~4 weeks later, so the first report with a verdict is expected on 2027-12-01). Before 250 dates the verdict is `insufficient_data` and the report shows the progress `n/250` (interim values are printed for information only and carry no decision). From 250 dates on, every report recomputes the verdict on the **same first 250 dates** (deterministic, no peeking, no optional stopping); later dates are ignored for the verdict (the report may show them as information). The first report with 250 dates is the official verdict and is recorded in LEARNINGS / DECISIONS_FEATURES.
- **Success criterion:** mean IC < 0 and one-sided p < 0.05 (t_NW ≤ −1.645) → `confirmed`. Significantly **positive** (Φ(−t_NW) < 0.05, i.e. t_NW ≥ +1.645) → `rejected`. Otherwise `inconclusive`.
- **Honest power note:** if the in-sample effect is real and stable, the expected t on 250 dates is about −3.2 · √(250/1040) ≈ −1.57, i.e. roughly 45–50 % power. An `inconclusive` result is therefore likely even with a real effect and does **not** disprove it (and if the in-sample estimate is inflated by selection from ~435 tests, power is lower).
- **Consequence (pre-specified):** `confirmed` → days-to-cover may be used as a component of the F1 composite score (negative sign). Otherwise it is not used as a standalone signal in F1 (it can stay a model input feature).

**Where it is computed:** monthly `feature_analysis` (`FeatureAnalysisEngine._test_h14`), stored in `analysis_reports.hypothesis_results['H14']` and shown in the HTML report. Unlike H1–H13 it uses its own fixed target (not the model target) and is reported with `effect_size` = mean IC (**negative** = hypothesised direction) and `pvalue` = **one-sided** Φ(t_NW) (close to 1 = positive IC); `n_dates` = dates used (≤ 250). The entry exists from the first report on (`0/250` until the first out-of-sample targets mature); a missing feature/target column yields `insufficient_data` with an explanatory detail instead of dropping the test silently.

---

## Feature Engineering: Temporal Patterns (Sprint 8 Requirement)

> **Key Insight (2026-05-05):** Point-in-time feature snapshots miss critical temporal patterns. A feature vector for a single day cannot capture signal **persistence**, **recurrence**, or **convergence** without explicit temporal aggregation features.

### The Problem
If we only provide `insider_cluster_score = 1591` for today's snapshot, the model cannot know:
- Was there also a cluster 10 days ago? (Recurrence)
- Has ARK been accumulating for 2 weeks straight? (Persistence)
- Did an analyst upgrade happen in the same month? (Convergence)

### Required Temporal Features by Signal Source

| Signal Source | Point-in-Time Feature | Temporal Features Needed |
|---|---|---|
| **Insider Clusters** | `cluster_score` | `cluster_count_30d/60d`, `cluster_score_sum_60d`, `days_since_last_cluster`, `cluster_recurrence_flag` |
| **ARK Holdings** | `delta_type`, `shares_delta` | `ark_increase_days_10d`, `ark_conviction_streak`, `ark_weight_trend_20d`, `n_etfs_holding` |
| **Analyst Ratings** | `latest_action` | `upgrades_30d`, `downgrades_30d`, `net_sentiment_30d/60d`, `upgrade_streak`, `consensus_trend` |
| **Politician Trades** | `latest_trade` | `politician_buy_count_60d`, `n_distinct_politicians_90d`, `bipartisan_flag` (both parties buying) |
| **13F Holdings** | `n_top_holders` | `n_holders_trend_qoq`, `new_positions_this_quarter`, `exited_positions_this_quarter` |
| **Fundamentals** | `pe_ratio`, `revenue_growth` | `pe_trend_4w`, `margin_trend_4w`, `revenue_acceleration` |
| **Earnings** | `eps_surprise_pct` | `consecutive_beats`, `surprise_trend`, `pre_earnings_insider_buying` |
| **Technical** | `rsi`, `sma_50` | Already temporal by design (moving averages) ✅ |

### Design Principle: Rolling Window Features
Every signal source should provide features in at least three time horizons:
- **Short:** 7–14 days (tactical momentum)
- **Medium:** 30–60 days (conviction/trend)
- **Long:** 90–180 days (structural shift)

This mirrors how professional analysts think: "Is this a one-off event, or part of a sustained trend?"

---

## Failed Approaches (for posterity)

Strategies we tried that proved non-functional. Just as important as confirmed signals to avoid repetition.

*(Empty so far)*
