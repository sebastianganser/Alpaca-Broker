# DECISIONS_FEATURES.md – Features, Scoring & Trading Decisions

> Decisions related to feature engineering, scoring models, trading strategy, and ML pipeline.
>
> See also: [INDEX.md](INDEX.md) · Related: [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) · [DECISIONS_DATA.md](DECISIONS_DATA.md)

**Last updated:** May 2026

---

### [2026-04-11] No Trading at Start – Signal Warehouse First

**Context:** The original video tutorial suggested starting directly with trading strategies (trailing stop, wheel, copy trading). After discussion it became clear that without a solid data foundation, every strategy relies on gut feeling.

**Decision:** First build Signal Warehouse (2–3 months data collection), then develop strategies based on real data.

**Rationale:**
- Data collection costs almost nothing, but past data is not retroactively obtainable
- Backtests need clean data that we must build first
- Without understanding signal quality we would be blindly copying (e.g., confusing ARK rebalances with conviction buys)
- Robustness was named top priority – requires data-driven decisions

**Revisit trigger:** If after 2 months the collected data proves too thin.

---

### [2026-04-11] Implementation Pace: Documentation First, Then Incremental

**Decision:** Complete project documentation first, then incremental sprint implementation.

**Rationale:** Sebastian explicitly requested this. Documentation forces clear thinking about the entire architecture.

---

### [2026-04-12] Feature Selection: All Three Methods in Parallel

**Decision:** Correlation (linear relationships), LASSO (automatic feature selection), and Random Forest Importance (non-linear patterns) – all three in parallel.

**Rationale:** If a feature is recognized as important by all three methods, that's a robust signal. Implementation effort is minimal, insight gain is high.

---

### [2026-04-12] Scoring Model: Stepwise Evolution

- **Phase 1 (start):** Equally weighted features → the only honest starting point without performance history
- **Phase 2 (after 3 months):** Performance-weighted based on observed correlation with returns
- **Phase 3 (after 12 months):** ML models like XGBoost when enough training data exists

**Rationale:** Overfitting risk with premature ML use. Honest baseline first.

**Revisit trigger:** At phase boundaries (3 months, 12 months) automatically.

---

### [2026-04-12] Portfolio Construction: Conservative Defaults

- Max 20 positions simultaneously (diversification without overhead)
- Equal-weighted, 5% per position
- Max 5% single position as hardcoded guardrail
- Weekly rebalancing (fewer transaction costs, less noise)
- Stop-loss at -10%, take-profit at +25%

**Rationale:** Kelly sizing or risk parity come only when reliable performance data exists. Simple, robust defaults at start.

**Revisit trigger:** After 3 months of paper trading based on actual performance.

---

### [2026-04-13] Price Backfill from 2021-01-01 via Alpaca

**Context:** For meaningful TA indicators (SMA 200 needs 200 days), ML training (500k+ samples), and backtesting.

**Decision:** Price backfill from 2021-01-01 (~5.3 years, ~882k rows). Signal backfill remains NO.

**Rationale:**
- Prices are base data, not signals – no "pseudo-alpha" risk
- Covers multiple market regimes (COVID recovery, 2022 bear, AI boom)
- Signal data (ARK, insider, politicians) is not historically available for free → stay synchronous from April 2026
- Feature store can cleanly handle NULL signals before April 2026

**Revisit trigger:** If asymmetric data availability measurably skews ML results.

---

### [2026-04-13] Relative Strength vs. SPY: Excess Return (Return Difference)

**Options:**
- A: Return ratio – `(ticker_ret / spy_ret) - 1` → division-by-zero risk
- B: Return difference (excess return) – `ticker_ret_20d - spy_ret_20d`
- C: Mansfield RS (price ratio, normalized) → shows trend, not magnitude
- D: IBD RS rating (weighted multi-period) → too complex for daily feature

**Decision:** Option B – excess return.

**Rationale:**
- No division-by-zero risk
- Intuitive: +0.05 = ticker outperformed SPY by 5 percentage points
- Standard in quantitative finance ("alpha")
- Linear scale, symmetric → better ML feature

---

### [2026-04-13] TA Indicators Job at 22:30 CET Daily

**Decision:** 22:30 CET – 15 minutes after price collector (22:15), 30 minutes before ARK (23:00).

**Rationale:** TA computer depends solely on `prices_daily`. Price collector takes <20s for 644 tickers. 22:30 gives conservative buffer.

---

### [2026-04-13] Sprint Order: Dashboard (Sprint 7) BEFORE Feature Pipeline (Sprint 8)

**Decision:** Dashboard first, Feature Pipeline second.

**Rationale:** Sebastian wanted immediate visual feedback after the next sprint. Backfill controls, DB cleanup, and scheduler overview must be available in the UI before the Feature Pipeline is built.

---

## Sprint 8 Decisions

### [2026-05-12] Feature Store: Wide Table with Temporal Rolling-Window Features

**Context:** Sprint 8's feature pipeline needs to aggregate all signal sources into a single table. Original schema had ~30 columns (point-in-time only). LEARNINGS_HYPOTHESES.md identified that point-in-time features miss critical temporal patterns (persistence, recurrence, convergence).

**Decision:** Wide table with ~77 columns, organized by signal source. Each source gets point-in-time features PLUS temporal rolling-window features in three horizons (short 7–14d, medium 30–60d, long 90–180d).

**Rationale:** Wide tables are standard for feature stores — they avoid JOINs during ML training. The ~77 columns are manageable (scikit-learn and XGBoost handle this easily). Column NULLs self-document which features are available.

---

### [2026-05-12] Politician Features: Dual-Date Computation

**Context:** Politician trades have both `transaction_date` (when the trade occurred) and `disclosure_date` (when it became public). For feature engineering, using `transaction_date` risks lookahead bias, but may carry stronger predictive signal if politicians trade on private information.

**Decision:** Compute BOTH variants for all politician features: `_disclosure` and `_transaction` suffixed columns. Evaluate which has higher predictive impact in Sprint 9 (Exploratory Analysis).

**Rationale:** Storage is cheap, retroactive computation is expensive. Having both variants from day one allows direct A/B comparison during feature importance analysis.

---

### [2026-05-12] NULL Handling: No Imputation at Feature Store Level

**Context:** Many feature columns will be NULL for most tickers (e.g., only ~200 tickers have ARK holdings, only ~650 have politician trades, 13F has quarterly gaps).

**Decision:** All feature columns are nullable. No imputation, no default values. The ML stage (Sprint 10) decides how to handle missing data (mean/median imputation, indicator columns, or model-native handling like XGBoost's built-in NULL support).

**Rationale:** Premature imputation (e.g., filling ARK features with 0 for non-ARK tickers) would introduce bias. The feature store should be a faithful representation of what we know, not what we assume.

---

### [2026-05-12] Scheduler Timing: 02:00 + 02:15 CET (Night Slot)

**Context:** Feature pipeline must run after all collectors and derived computers. The latest daily collector (Form 4) runs at 23:30, and the analyst ratings collector runs at 01:00 for ~10 minutes.

**Decision:** Feature pipeline at **02:00 CET**, target backfill at **02:15 CET**. Both run during the night slot when NY (closed since 22:00 CET) and London (closed since 17:30 CET) markets are closed.

**Rationale:** Ensures all collector data is fresh. Night slot minimizes DB contention. 45 minutes gap after analyst ratings provides safety margin.

---

### [2026-05-12] Target Variable Backfill: Separate Job, Manual Initial Run

**Context:** Target returns (`return_1d`, `return_5d`, `return_20d`, `return_60d`) can only be computed retrospectively when future prices exist. The initial historical backfill across all dates will be very long-running.

**Decision:** Target backfill runs as a separate daily job (02:15 CET) for incremental updates. The initial historical backfill is triggered manually from the Settings UI.

**Rationale:** Separating target backfill from feature computation keeps the daily job fast (new targets only need 60 days of lag). Manual initial run prevents a 30+ minute job from blocking the first deployment.

---

### [2026-05-12] Feature Pipeline Architecture: Independent Feature Groups

**Decision:** Each signal source is computed by an independent private method (`_compute_ark_features()`, `_compute_insider_features()`, etc.). If one method fails (e.g., no data), all others still execute.

**Rationale:** Follows the existing pattern (collectors are independent jobs). A ticker missing ARK data should still get insider, analyst, and technical features. Graceful degradation over all-or-nothing.

---

## Pending Decisions

- ML model selection (Sprint 10, after sufficient data)
- Feature importance evaluation: which politician date variant has higher impact (Sprint 9)
- Live trading activation criteria (Sprint 12)

