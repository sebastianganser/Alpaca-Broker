# DECISIONS_FEATURES.md – Features, Scoring & Trading Decisions

> Decisions related to feature engineering, scoring models, trading strategy, and ML pipeline.
>
> See also: [INDEX.md](INDEX.md) · Related: [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) · [DECISIONS_DATA.md](DECISIONS_DATA.md)

**Last updated:** 17 October 2026

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

## Sprint 8c Decisions

### [2026-05-13] Sentiment Model: FinBERT over Claude Haiku (Initial)

**Context:** Need NLP sentiment scoring for financial news headlines. Options: Claude Haiku API (~$10/month), ProsusAI/finbert (free, local), or VADER (rule-based).

**Decision:** Start with ProsusAI/finbert (110M params), local CPU inference. Plan Haiku upgrade for Sprint 9+ once pipeline proves stable.

**Rationale:**
- Zero cost (local model, no API calls)
- Latency: ~50-200ms per headline, batch processing (~23s for 1700 scores)
- Domain-specific: Trained on financial text (vs. general-purpose VADER)
- No external dependency during waiting phase
- Haiku upgrade remains a viable future enhancement

---

### [2026-05-13] Headline-Only Scoring (No Full Article Text)

**Context:** Many news articles are behind paywalls. Scoring could use: A) full article text (requires paywall bypass), B) headline + summary, or C) headline only.

**Decision:** Score headlines only. Summaries are optionally available but not used for scoring.

**Rationale:**
- Headlines carry the most information density for sentiment
- Avoids paywall issues entirely
- Consistent data quality (every article has a headline, not all have summaries)
- FinBERT's 512-token limit makes full articles impractical anyway

---

### [2026-05-13] Feature Pipeline Date Filter: published_at over scored_at

**Context:** Initial implementation filtered sentiment data by `scored_at` (when FinBERT ran). This caused empty features because the pipeline computes for yesterday but all scores have today's `scored_at`.

**Decision:** JOIN with `news_articles` table and filter on `published_at` (when the article was published). This correctly associates articles with their temporal context regardless of when scoring occurred.

**Lesson:** Always distinguish between "when did the event happen" (published_at) vs "when did we process it" (scored_at) in temporal feature engineering.

---

### [2026-05-13] Log Retention: 90 Days Automatic Cleanup

**Decision:** Collection logs older than 90 days are automatically deleted daily at 03:30 CET.

**Rationale:** Without retention, the `collection_log` table grows unboundedly (~15 entries/day × 365 = 5,475/year). 90 days provides sufficient operational history for debugging while keeping the database lean.

---

### [2026-08-18] Candidate Pipeline: Guardrails, Output Format and Pack Location

**Context:** Answers (Sebastian) to the open design questions of the data-extension concept ([§5.1, §6.3](2026-08-18_Konzept_Datenerweiterung.md)).
**Decision:**
- **Guardrails:** adopt the proposed hard filters (minimum liquidity, max. 2 candidates per sector, pairwise-correlation limit, churn lock, minimum score; "no candidates today" is a valid result) – but **do not hard-code the thresholds**; they must be configurable through the settings in the UI.
- **Output format:** one folder per day with an overview and up to 5 candidate Markdown files (`00_uebersicht.md`, `01_<TICKER>.md`, …).
- **Pack location:** `/mnt/user/Workfiles/AlpacaBroker` on Unraid; the path is an environment setting (`CONTEXT_PACK_PATH`) to stay portable.

**Status:** Output format and path are implemented (F2 MVP, Sprint 9.5c). The guardrails and their UI-configurable thresholds are still open under F1 (ROADMAP); the stage-1 risk pre-filter currently uses fixed constants in `derived/context_pack_generator.py` (ATR 1–3 %, earnings window, dollar volume > 0).

---

### [2026-09-04] Options IV + Estimates as Feature Groups (Migration 028)

**Context:** The options IV and estimates collectors (Sprint 9.5b/9.5a) wrote raw tables, but the feature pipeline did not use them, and the readiness script only checked 8 of the planned feature groups.
**Decision (commit `bda67d8`):** 9 new `feature_snapshots` columns – `options_iv_atm_30d`, `options_iv_skew_25d`, `options_iv_term_slope`, `options_iv_put_call_oi`, `eps_revision_pct_30d`, `eps_revision_pct_90d`, `revenue_revision_pct_30d`, `eps_revisions_net_7d`, `eps_revisions_net_30d`. Readiness script extended to all feature groups plus a per-source maturity check (minimum history days per source). Follow-up `4409caf`: features API + schemas exposed the new groups (they had been missing in the UI).
**Note:** IV-Rank needs ~252 sessions of IV history and is tracked as informational only (IV data since 2026-08-31 → ~September 2027).

---

## Review 2026-10-05 Decisions (Opus 5.5 Code Review)

### [2026-10-05] Target Definition: Entry at Next Open
**Context:** Features are computed at 02:00 CET from information that partly arrives after the US close (news, EDGAR filings until 22:00 ET). The old targets `close(d) → close(d+h)` therefore leaked the overnight gap.
**Decision:** `return_h = close(d+h) / open(d+1) − 1` for h ∈ {1, 5, 20, 60} trading days on the NYSE calendar. All old targets were set to NULL in migration 030 and recomputed.

### [2026-10-05] Information Cut-offs (Point-in-Time)
- **News:** only articles with `published_at` < 16:00 ET on d (stored as naive UTC).
- **Form 4:** public if `acceptance_datetime` < 16:00 ET on d, otherwise `filing_date` ≤ d. EDGAR clock times are interpreted as US/Eastern (conservative).
- **Insider clusters:** rebuilt as of d only from filings public by d; stored clusters carry `known_date`.
- **Politicians:** every query requires `disclosure_date` ≤ d (including the `_transaction` variants).
- **13F:** `filing_date` ≤ d, only `put_call = 'SH'`.
- **Macro (FRED):** daily series up to the previous session; DTWEXBGS with 7-day lag; max age 14 days.
- **Earnings dates:** `first_seen` ≤ d (legacy rows keep a documented lookahead, capped at 90 days).
- **Short interest (FINRA, 2026-10-16):** the payload has no publication date, so a settlement date is usable for d only if it is ≤ the 9th NYSE session before d (`SI_PUBLICATION_LAG_SESSIONS`; FINRA publishes ~7 business days after settlement + 2 sessions margin); newest usable settlement older than 40 days → NULL. See [DECISIONS_DATA.md](DECISIONS_DATA.md) 2026-10-16.
- **Forward-fill limits:** 14 days (fundamentals, estimates, IV, TA), 10 days ARK, 152 days 13F.

### [2026-10-05] Feature Versioning & Upsert Semantics
Every row stores `feature_version` (currently `2026.10-3`, `FEATURE_VERSION` in `derived/feature_pipeline.py`) and `computed_at`. The upsert overwrites **all** feature columns including NULL, so recomputes repair stale values. Snapshots and targets exist **only for NYSE trading days**.

### [2026-10-05] NULL vs. 0
`0` = the source covers the ticker but nothing happened; `NULL` = no coverage. Extrapolated (gap-filled) prices are never used for TA, targets, liquidity or breadth.

### [2026-10-05] Analysis Methodology
Daily cross-sectional rank IC (mean, ICIR, Newey-West t with lag ≥ h−1), per-date quintiles, purged + embargoed walk-forward CV grouped by date, date-block bootstrap, imputer/scaler fitted inside folds. Analysis window starts at `ml_start_date()` (retention cutoff + indicator warm-up). Context-pack score uses per-date centered ranks; weights are **provisional** until R1 is re-run on the rebuilt feature store.

### [2026-10-06] Trading Goal & Barrier Evaluation
**Context:** The purpose is 0–5 long candidates per day for short trades (≤ 14 trading days, take profit +0.5–1 % net). The context pack always emits 5 candidates from unvalidated weights, and the analysis targets fixed-horizon returns, which do not match a take-profit/stop trade.
**Decision:** Evaluate candidates with a barrier model: entry `open(d+1)`, take profit a ∈ {0.5, 0.75, 1.0} %, stop = 2–3 × a, time stop 14 trading days, same-day TP+SL → stop, gaps filled at open, 0.05 % round-trip costs. Benchmark = random hit rate b/(a+b) and the universe average. "No candidates" must be a valid output (threshold + market filter). See [2026-10-06_Konzept_Kurzfrist_Kandidaten.md](2026-10-06_Konzept_Kurzfrist_Kandidaten.md). **Scope clarified 2026-10-17** (see "Scope: Alpaca-Broker forecasts, a separate bot trades" below): Alpaca-Broker defines no trade rules; the barrier label is to be made strategy-neutral in a planned step.

### [2026-10-16] Forward Testing Waits for the Sprints (Data First)
**Context:** The stage-2 forward test (K5) is deployed since 2026-10-07 (`stage2_review` runs nightly), but no `decisions.yaml` has been written.
**Decision (Sebastian):** Forward testing and the related tests wait until the sprints are through and the data supports them – first enough data for Sprint 10 (ML scoring), measured with `scripts/sprint10_readiness.py`. Until then no `decisions.yaml` is expected; "0 decisions read" in `stage2_auswertung.md` is the intended state, not an issue. The nightly `stage2_review` step stays deployed (harmless without input).

### [2026-10-17] Pre-registration of H14 (Days-to-Cover)
**Context:** The first FINRA short-interest evaluation (monthly analysis of 2026-10-17, in-sample window 2022-07-28 … 2026-10-16) showed a consistently negative daily rank IC of `short_interest_days_to_cover` (`return_20d` rho −0.029, t_NW −3.20, 1,040 dates; `return_60d` t_NW −3.40; `return_barrier_14d` t_NW −1.85), but not Bonferroni-significant (~435 tests). All data up to 2026-10-16 has been looked at, so any further test on it is exploratory.
**Decision (Sebastian):** Register **H14** ("higher days-to-cover predicts lower returns") as a confirmatory test on data that did not exist at the time of the look, tracked automatically by the monthly `feature_analysis` (`hypothesis_results['H14']`). Design: feature frozen at `FEATURE_VERSION` 2026.10-3; decision target `return_20d` (barrier label reported for information only); snapshot dates ≥ 2026-10-19; mean daily rank IC with Newey-West t (lag 19), **one-sided** p = Φ(t_NW); **fixed n = the first 250 valid out-of-sample dates, evaluated once** – before that `insufficient_data` (progress n/250), afterwards always the same 250 dates; `confirmed` at mean IC < 0 and p < 0.05, `rejected` at a significantly positive IC, otherwise `inconclusive`. Full design: [LEARNINGS_HYPOTHESES.md](LEARNINGS_HYPOTHESES.md) H14.
**Why this shape:** A fixed sample size and a single look rule out optional stopping and peeking (the verdict cannot be tuned by waiting for a good-looking interim value); the one-sided test is justified because the direction was fixed before the out-of-sample data exists (and matches the literature) and keeps the power as high as the sample allows. The expected t on 250 dates is only ≈ −1.57 if the in-sample effect is real and stable, i.e. ≈ 45–50 % power – an `inconclusive` result is likely and is **not** evidence against the effect; waiting for more dates or relaxing the criterion afterwards is explicitly not allowed (a new test would have to be pre-registered again).
**Consequence for F1:** only a `confirmed` verdict lets days-to-cover enter the F1 composite score (negative sign); otherwise it is not used as a standalone signal (it can remain a model input feature). The first report with 250 dates (expected 2027-12-01) is the official verdict. Changing the feature definition, the target, the sample start or the statistic invalidates the registration (tripwire test `test_h14_constants_are_the_pre_registered_design`).

### [2026-10-17] Trading Goal Extended: Medium-Term Trend Trades with Trailing Stop – SUPERSEDED the same evening
> **SUPERSEDED (2026-10-17, evening):** Sebastian clarified the scope right after this entry was implemented (commit `608674c`): Alpaca-Broker does **not** define strategies, trailing stops or other exit rules – it forecasts which tickers are expected to rise (later fall), and a separate bot trades. The trailing-stop label was therefore removed again (migration 038, code deleted, model target `return_60d`) – see "Scope: Alpaca-Broker forecasts, a separate bot trades" below. The text below is kept as the record of what was built and why; exit rules are not part of this project's scope. What stays valid: the 60-day horizon as the primary forecast horizon (the t-values in the table below are against the plain `return_60d`, not against the trailing label).

**Context:** The goal so far was the short barrier trade (+1 % / −2 % / 14 sessions, `return_barrier_14d`, migration 033); the research rounds K1–K4 found no usable edge at that horizon. The monthly `feature_analysis` of 2026-10-17 (repaired data, 2022-07-28 … 2026-10-16) shows first hints of a signal at a longer horizon – daily rank IC against `return_60d`:

| Feature | `return_60d` t_NW | 14-day barrier |
|---|---|---|
| `insider_buy_ratio_30d` | +3.0 | ≈ 0 |
| `insider_buy_value_30d` | +2.8 | ≈ 0 |
| `form13f_top_holder_count` | +2.3 | ≈ 0 |
| `short_interest_days_to_cover` | −3.4 | −1.85 |

None of these is Bonferroni-significant (~435 tests) and all data up to 2026-10-16 has been looked at, so they are hypotheses for the new target, not confirmed signals (the H14 pre-registration is unaffected).
**Decision (Sebastian):** Extend the goal: besides the short barrier trade the project now targets **medium-term trend trades with a trailing stop** – identify tickers with a positive future trend; smart-money signals (insider, 13F, short interest, ARK) may act over weeks or months, not 14 days. Parameters: trail distance **3 × ATR(14)**, fixed per trade; **max holding 60 sessions**; 0.05 % round-trip cost (as for the barrier label).
**Label** (`analysis/trailing.py` `MAIN_TRAILING`, persisted by `derived/target_backfill.py`, migration 037):
- Signal date d, entry `E = open(d+1)`; trail distance `D = 3 × ATR14(d)` in price units, fixed for the whole trade (= Alpaca `trail_price`). ATR = the stored `technical_indicators.atr_14` of d (Wilder ATR(14) over real, non-extrapolated bars, data up to and including d only – no lookahead); a missing or non-positive ATR leaves the label NULL.
- High-water mark `HWM` starts at `E`. For session k = 1..60: `stop_k = HWM_{k−1} − D` (`HWM_0 = E`). `k ≥ 2` and `open_k ≤ stop_k` (gap through the stop) → exit at `open_k`; else `low_k ≤ stop_k` → exit at `stop_k`; else `HWM_k = max(HWM_{k−1}, high_k)`. Not stopped after session 60 → exit at `close_60` (time stop). `return_trail_60d = exit / E − 1 − 0.0005`; `trail_exit_day` 1..60 (time stop = 60); `trail_stopped` true = trailing stop, false = time stop.
- **Daily-bar approximation:** the stop of session k uses the high-water mark up to session k−1 only; the high of session k raises the stop from session k+1 on. Alpaca's real trailing stop follows the price intraday, so a day that makes a new high and then falls D below it would be stopped out in reality but not in the label – the label is slightly optimistic on such round-trip days (only daily bars exist). Intraday stop fills carry no slippage.
- **Window rule = barrier rule:** the label stays NULL until all 60 sessions after d exist as real bars (NYSE sessions; missing, extrapolated or non-positive bars → NULL). A trade that has already stopped out is deliberately not labelled early – otherwise the recently labelled dates would be skewed towards losers.
**Consequences:**
- The barrier label and its tools stay as a variant (`walkforward_hit_model.py`, `backtest_barrier.py`, event studies, the context-pack ATR filter and stage 2 are barrier-specific; adapting them to trend trades is a later step).
- Model target of the monthly `feature_analysis` switched: `MODEL_TARGET = 'return_trail_60d'` (horizon 60: Newey-West lag 59, purge 60 dates, "reliable" from 600 dates) with the fallback chain trail → barrier 14d → `return_20d` under the existing coverage rule (≥ 50 % of the `return_20d` rows). H14 is unchanged (own target `return_20d`, barrier as secondary; tripwire test). `FEATURE_VERSION` is not bumped – the new columns are targets/labels, not features.
- Execution phase (later, not now): the exit is meant to be Alpaca's native `trailing_stop` order (`trail_price` = 3 × ATR(14) at entry, or `trail_percent`); paper account only, orders only with Sebastian's approval.
- Limits: (1) labels exist only 60 sessions after d – the newest ≈ 3 months are unlabelled; (2) overlapping 60-session windows leave ≈ 21 independent periods in the 5-year window, so significance stays weak; (3) the known delisting limit grows from ≈ 20 to ≈ 60 sessions (abruptly vanishing tickers get no label for their last 60 sessions, the worst outcomes are underrepresented); (4) the first ≈ 14 sessions of a ticker's price history have no ATR → NULL; (5) 3 × ATR(14) and 60 sessions are fixed a priori, not tuned on the data.

### [2026-10-17] Scope: Alpaca-Broker Forecasts, a Separate Bot Trades
**Context:** The same evening the trailing-stop trend-trade label (entry above, `608674c`, migration 037) was built and deployed, Sebastian clarified what the project is for. The barrier label (2026-10-06) and the trailing label both encode a trade strategy (entry, take profit / stop / trailing stop, time stop) into a target – that was a misunderstanding of the scope.
**Decision (Sebastian):**
- **Goal of Alpaca-Broker:** from the collected data, identify tickers whose prices are expected to **rise over the next days to months** (later also tickers expected to **fall**). The collected data is handed to Claude via the context pack, prompted to judge the tickers like a Wall-Street broker.
- **Forecasts are strategy-neutral:** plain forward returns `return_5d` / `return_20d` / `return_60d` (`close(d+h) / open(d+1) − 1`); **primary horizon 60 sessions ("first of all months"), then 20**. No exit rule is part of a target.
- **Strategy selection, entry/exit rules (trailing stop, take profit, …) and the actual trading belong to a separate, future bot** – not to Alpaca-Broker. Paper trading only, as before; orders only with Sebastian's approval.
**Consequences:**
- Trailing label removed again: migration **038** drops `return_trail_60d` / `trail_exit_day` / `trail_stopped` (037 is applied in production and stays untouched); `analysis/trailing.py`, the trailing parts of `derived/target_backfill.py`, the ORM columns and the trailing tests are deleted.
- Model target of the monthly `feature_analysis`: `MODEL_TARGET = 'return_60d'` (horizon 60) with the fallback `return_20d` (horizon 20) under the existing coverage rule (≥ 50 % of the `return_20d` rows); the barrier label is no longer part of the chain (`MODEL_TARGET_CHAIN`). The 60-day horizon means Newey-West lag 59, purge 60 dates and "reliable" only from 600 dates; overlapping windows leave ≈ 21 independent periods in the 5-year window, so significance stays weak (as already noted for the trailing label). H14 (own target `return_20d`, barrier only as an information-only secondary) fits the new scope and is unchanged.
- **Planned, not done yet (separate step, plan follows):** make the older strategy-bound parts neutral – the 14-day barrier label (`analysis/barrier.py`, `return_barrier_14d` and its tools `walkforward_hit_model.py`, `backtest_barrier.py`, event studies), the context pack (its stage-1 ATR/earnings pre-filter and "0–5 candidates for short trades" logic come from the barrier concept) and the stage-2 forward test K5. Until then they stay as they are.
- Lesson: ask for the scope before encoding strategy rules into targets (see [LEARNINGS.md](LEARNINGS.md) 2026-10-17).

### Known Limits
- `universe.sector` is not point-in-time.
- A 13F filed on day d counts as public on d.
- Legacy earnings rows (before `first_seen` existed) retain lookahead up to 90 days.

---

## Pending Decisions

- ML model selection (Sprint 10, after sufficient data)
- Feature importance evaluation: which politician date variant has higher impact (Sprint 9)
- FinBERT → Haiku upgrade: when sentiment data volume justifies API costs (Sprint 9+)
- Live trading activation criteria (Sprint 12)
