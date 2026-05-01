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

---

## Failed Approaches (for posterity)

Strategies we tried that proved non-functional. Just as important as confirmed signals to avoid repetition.

*(Empty so far)*
