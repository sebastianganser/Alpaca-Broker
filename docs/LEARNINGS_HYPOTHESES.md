# LEARNINGS_HYPOTHESES.md – Hypotheses & Planned Investigations

> Forward-looking research agenda: hypotheses to test and investigations to run once sufficient data has been collected.
>
> See also: [INDEX.md](INDEX.md) · [LEARNINGS.md](LEARNINGS.md)

**Last updated:** May 2026

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
