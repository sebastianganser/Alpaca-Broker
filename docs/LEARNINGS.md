# LEARNINGS.md – Observed Findings & Data Quality

> Living document. Grows as we collect and analyze data.
> For hypotheses and planned investigations, see [LEARNINGS_HYPOTHESES.md](LEARNINGS_HYPOTHESES.md).
>
> See also: [INDEX.md](INDEX.md)

**Last updated:** 18 October 2026

---

## Entry Format

```markdown
### [YYYY-MM-DD] Title

**Observation:** What did we see?
**Data:** What data basis?
**Hypothesis:** What could be behind it?
**Next steps:** How to verify or refute?
**Status:** 🟡 Open / 🟢 Confirmed / 🔴 Refuted
```

**Categories:** 📊 Data Quality · 🎯 Signal Strength · 🔬 Patterns · ⚠️ Pitfalls · 🛠️ Technical

---

## Findings

### [2026-04-15] 📊 First Politician Trade Data: High-Frequency Senators

**Observation:** In the first successful Senate eFD import (161 PTR filings, 636 transactions), a few senators trade extremely actively. John Boozman had at least 10 transactions on 14 April alone. John Fetterman shows a similar pattern with 8+ trades on 3 April.

**Data:** 636 politician trades from 161 PTR filings (12-month lookback).

**Hypothesis:** These senators actively diversify their portfolios. The high transaction frequency with small amounts ($1,001–$15,000) suggests regular rebalancing – probably **not a strong alpha signal** for individual trades. More interesting would be large single trades (>$50,000).

**Next steps:** After 1 month: categorize by amount, frequency analysis per senator.

**Status:** 🟡 Open

---

### [2026-04-15] 📊 yfinance Format Inconsistency: dividendYield on Different Scale

**Observation:** TSM showed 95% dividend yield in the dashboard. Root cause: yfinance delivers `dividendYield` in percent form (0.92 = 0.92%), while `profitMargins`, `operatingMargins` etc. come as decimal (0.451 = 45.1%).

**Data:** Systematically verified across 6 tickers (AAPL=0.4, MSFT=0.93, TSM=0.92, JNJ=2.19, GOOG=0.25 – all already percent values).

**Lesson:** Yahoo Finance API delivers different fields at different scales. Since yfinance is an unofficial wrapper, formats can change any time. → **Defensive programming with plausibility checks is mandatory.**

**Status:** 🟢 Confirmed & fixed (migration 013 + plausibility validation)

---

### [2026-04-15] 📊 Architecture Gap: Tickers Without Universe Entry Have No Data

**Observation:** SIRI (via politician trade by Hickenlooper) was stored in `politician_trades` but had no prices, indicators, or fundamentals in the dashboard. Root cause: politician trades collector didn't add tickers to the universe.

**Solution:** `NewTickerOnboarder` (`universe/onboarder.py`) – Alpaca validation + automatic backfill pipeline (Prices → TA → Fundamentals → Sector).

**Status:** 🟢 Fixed (Session 14)

---

### [2026-04-15] 🛠️ Doc Schema ≠ ORM Model ≠ API Schema: Triple Mismatch (ARK Deltas)

**Observation:** The `ark_deltas` table had three different "truths": ARCHITECTURE.md defined different columns than the ORM model, and the API schema expected the doc version.

**Lesson:** For every new API endpoint: treat the ORM model as single source of truth, align schema/route against it. Consider integration tests with real DB queries.

**Status:** 🟢 Fixed (Session 15)

---

### [2026-04-15] 📊 ARK Deltas: `unchanged` Is Not a Signal

**Observation:** With 322 holdings and 2 consecutive snapshots, 322 deltas were computed. ~251 were `unchanged`. Only ~22% of positions have real daily share movements.

**Hypothesis:** Most ARK positions don't change on a normal trading day. Weight deltas (from ETF NAV changes) are more frequent than share deltas (from active trades).

**Status:** 🟡 Open

---

### [2026-04-19] 📊 ARK Trade Emails ≠ Holdings Deltas: Net Exposure vs. Portfolio Manager Trades

**Observation:** ARK sends an email: "ARKW: Buy NFLX 26,161 Shares". Our dashboard shows: ARKW NFLX **REDUCED** (Shares Δ = -3,464). Contradiction?

**Analysis:** ARK's email disclaimer states trades exclude ETF creation/redemption activity. Net position was -3,464 shares (ARK bought +26,161 but redemptions caused -29,625).

**Implication:** Our snapshot-based delta shows the **actual net exposure** – more relevant for signals than trade intention. A positive shares delta (net increase) is a stronger bullish signal than an ARK buy alone.

**Status:** 🟢 Not a bug – design is correct. Documented for signal interpretation.

---

### [2026-04-16] 🛠️ SEC 13F Infotable: No Standardized Filename

**Observation:** 6 of 20 top filers delivered 0 holdings. Examples: Citadel ✅ `infotable.xml`, Two Sigma ❌ `informationtable.xml`, Berkshire ❌ `50240.xml`, Millennium ❌ `MLP_Filing_*.xml`.

**Solution:** 4-stage detection: `infotable` → `informationtable` → `holding` → largest non-primary XML.

**Status:** 🟢 Fixed (Session 17)

---

### [2026-04-16] 📊 Plausibility Ranges: Format Guard ≠ Value Filter

**Observation:** First production run showed 138 warnings at 670 tickers. All values were **real** – negative P/B at MCD/SBUX/BKNG (buyback programs), negative forward P/E at MRNA/OKLO (expected losses), extreme margins at pre-revenue firms like ACHR (-781%).

**Lesson:** Plausibility checks should only catch **format errors and data corruption**, not legitimate extreme values. Only exception: `dividend_yield [0, 0.25]` as regression guard.

**Status:** 🟢 Fixed (Session 17)

---

### [2026-04-16] 🛠️ yfinance Logs ERRORs for Expected Cases

**Observation:** Earnings calendar collector showed 5 ERROR messages from yfinance's internal logger: "No earnings dates found" for BRK.B, GENB etc.

**Solution:** `logging.getLogger("yfinance").setLevel(logging.CRITICAL)` – only real crashes come through. Own error handling logs true positives as WARNING.

**Status:** 🟢 Fixed (Session 17)

---

### [2026-04-16] 🛠️ datetime.date vs pd.Timestamp: Silent Index Lookup Failures

**Observation:** TA computer wrote 0 records daily despite backfills working. Root cause: `date(2026,4,16) == Timestamp('2026-04-16')` evaluates to `False` in Python.

**Lesson:** Always use `pd.Timestamp(target_date)` for DataFrame index lookups. Never compare SQL return types (`datetime.date`, `Decimal`) directly with pandas types.

**Status:** 🟢 Fixed (explicit `pd.Timestamp()` conversion)

---

### [2026-04-16] 🛠️ Jobs Without CollectionLog Are Invisible

**Observation:** TA job ran 2+ weeks with errors (0 records), completely invisible in dashboard. Only job without `CollectorLogCapture`.

**Lesson:** Every new job must use the CollectorLogCapture pattern from day 1. Silent failures are worse than loud errors.

**Status:** 🟢 Fixed (TA job now uses CollectorLogCapture)

---

### [2026-04-30] 🛠️ MAX(trade_date) Is Insufficient as Completeness Check

**Observation:** TA catchup reported "Already up-to-date" despite 0 records for 670 tickers on that date. A single partial record was enough to mark the entire day as "done".

**Lesson:** For derived-data jobs producing many records per date, use coverage comparison (`COUNT(DISTINCT ticker)` per date vs. expectation) instead of MAX(). Costs 2 extra queries, prevents multi-day invisible gaps.

**Status:** 🟢 Fixed (two-phase catchup with coverage check)

---

### [2026-05-02] 📊 SEC Form 4: Transaction Dates Can Be Wildly Incorrect

**Observation:** After full insider backfill (313k trades), `transaction_date` ranged from year 0024 to 2033. Root causes:
- **Year 0024/0025:** Filer typos in SEC XML (SPGI: "0024-02-01" instead of "2024-02-01")
- **2008/2009 dates:** CTAS derivatives filed in 2025 but referencing old grant dates (amendments)
- **2033/2028:** Future vesting schedules for stock options (BEAM, TMUS, CRDO)

**Lesson:** `filing_date` from SEC metadata is reliable. `transaction_date` from the XML body is user-entered and should always be validated against `DATA_START_DATE..today`. Cleanup is essential before any aggregation or ML pipeline.

**Status:** 🟢 Fixed (cleanup_insider_outliers.py removes 392 records)

---

### [2026-05-05] 🛠️ Readiness Scripts Must Check Depth and Coverage, Not Just Row Counts

**Observation:** Sprint 8 readiness script reported "Insider-Trades: 24,552 (gut, >500 reicht)" – but 50% of universe tickers had only 7 days of data from the daily collector. The backfill gap was invisible because `COUNT(*)` doesn't reveal distribution.

**Lesson:** Readiness/health checks need three dimensions:
1. **Volume:** Total row count (existing check)
2. **Depth:** Does `MIN(date)` reach back to the expected start?
3. **Coverage:** What % of active tickers have data?

Without depth + coverage, a single high-activity ticker can mask the absence of data for hundreds of others.

**Status:** 🟢 Fixed (sprint8_readiness.py rewritten with depth + coverage per table)

---

### [2026-05-13] 🛠️ Computed Fields Missing in Ticker-Specific Endpoint (delay_days)

**Observation:** The Politician Trades tab showed `delay_days = null` (displayed as "—") when viewing a ticker-filtered signal (e.g., LBRDK via `/ticker/LBRDK/signals`). The global endpoint (`/signals/politicians`) correctly computed `delay_days: 351`, but the ticker-specific endpoint (`/ticker/{symbol}/signals`) omitted the calculation entirely.

**Root cause:** The `PoliticianTradeItem` construction in `ticker.py` did not include `delay_days`. The field defaults to `None` in the Pydantic schema, so no validation error occurred – the bug was completely silent.

**Data context:** The affected record (John W. Hickenlooper, LBRDK, trade 2025-05-19 → disclosure 2026-05-05) had a legitimate 351-day delay. Three Hickenlooper trades showed 336–384 day delays, well beyond the STOCK Act's 45-day requirement.

**Lesson:** When the same data type is served by multiple endpoints (global list vs. ticker-specific), **all computed/derived fields must be replicated**. Silent defaults (`None`) in Pydantic schemas make such omissions invisible. Consider extracting shared serialization helpers to prevent divergence.

**Status:** 🟢 Fixed (commit `0a9be3b`)

---

### [2026-08-18] 📊 Politician Trades: Disclosure Lags Far Beyond the 45-Day STOCK Act Deadline

**Observation:** In the Signals view of 2026-08-18 some senators disclose trades extremely late: Tuberville reported 2024 trades with a lag of **824 days**, Wyden with **465 days** (see also the Hickenlooper case above). Such a trade has no information value but dilutes every aggregate that counts trades. The same review found an empty party column and a parser artefact (`--AMCR`).
**Lesson:** The `delay_days` column was already there but not used as a weight or filter. Politician features must restrict themselves to trades with a short lag (H7: politician trades may be too delayed for alpha).
**Status:** 🟢 Acted on in Sprint 9.5c: hard filter at 45 days (C1), ticker parser fix (C2), party mapping (C3). A softer variant (exponential decay by lag instead of a cut-off) was not implemented.

---

## Meta-Learnings

### [2026-04-15] 🛠️ TLS Fingerprinting is Becoming Standard on Government Sites
Senate eFD blocks Python `requests` via JA3 hash detection, not header analysis. Solution: `curl_cffi` with Chrome impersonation. Expect more government and finance sites to use similar bot detection.

### [2026-04-15] 🛠️ DataTables Server-Side Processing Requires Session Context
Senate eFD renders no server-side HTML tables – empty template + JavaScript/AJAX. The AJAX endpoint requires a prior search form POST to set parameters in the server session.

### [2026-04-15] 🛠️ SEC Archives Under Subject-CIK, Not Filer-CIK
The accession number contains the filer CIK (often a law firm), but files are stored under the subject company CIK.

### [2026-04-28] 🛠️ Automatic ETF Filters Can Deactivate Benchmarks
The ETF filter correctly identified SPY as ETF and deactivated it. But SPY is needed as benchmark for `relative_strength_spy`. Fix: `BENCHMARK_TICKERS` protection set. **Lesson:** Automatic filters always need an allowlist for exceptions.

### [2026-05-02] 🛠️ Long-Running Backfills Must Survive Session Loss
First insider backfill (~30h) was interrupted by a Windows update killing the SSH session. **Lesson:** Always run long processes via `tmux` or `nohup` on the server. Implement resume-safe logic (check what's already done, skip completed items) for any multi-hour batch job.

### [2026-05-13] 🛠️ Dual-Endpoint Parity: Shared Serialization Prevents Silent Drift
When the same data model is served by two endpoints (e.g., `/signals/politicians` and `/ticker/{symbol}/signals`), computed fields added to one are easily forgotten in the other. Pydantic's `Optional` defaults mask this. **Lesson:** Extract a shared `_serialize_politician_trade(t)` helper function to guarantee parity. Add integration tests that compare field completeness across equivalent endpoints.

### [2026-10-06] 📊 Barrier-Backtest K1: Current Context-Pack Score ≈ Random
Backtest 07/2022–10/2026 (1,051 signal dates, 515k universe trades), entry `open(d+1)`, TP 0.5–1 %, SL 2–3 × TP, max 14 sessions, 0.05 % costs ([concept](2026-10-06_Konzept_Kurzfrist_Kandidaten.md)).
- **Daily bars cannot resolve tight barriers:** 24–36 % of 0.5 %/1 % trades touch TP and SL on the same day (avg. holding 1.0–1.2 days). The conservative "stop first" rule then dominates (universe hit rate 51 % vs. 66.7 % random). With the OHLC-path heuristic the universe matches the random-walk baseline almost exactly (hit rate ≈ b/(a+b), net ≈ −costs) → heuristic is well calibrated; truth lies between both rules.
- **Top 5 of the provisional score:** no advantage over the universe under the OHLC rule (the "significantly worse" result under the conservative rule was an artefact of more volatile picks → more ambiguous days). **Bottom 5** slightly worse (−0.03 %, significant in 3/6 combos) → at most a weak signal at the bottom end.
- **Filters (ATR 1–3 %, no earnings in window):** +0.05 % significant only under the conservative rule, +0.00–0.02 % n.s. under OHLC → mostly fewer ambiguous days, not a proven edge. **Regime filter** (SPY > SMA200, VIX < 25): no improvement.
- **Lesson:** Evaluate TP/SL trades under both ambiguity rules; prefer barriers ≥ 1 % (ambiguity 1–5 %) when only daily bars exist. Hand-tuned weights are not a selection model – candidates must come from validated rankers (K2).

### [2026-10-06] 📊 Feature Analysis on the Barrier Label (K2): No Robust Signal Yet
First analysis run with `return_barrier_14d` (+1 % / −2 % / 14 sessions, net) as model target: 527k snapshots, 684 tickers, 07/2022–10/2026, purged walk-forward (purge 14 dates).
- **Daily rank IC:** 0 of 74 features significant (same as for `return_20d`). Best feature with full history: `atr_14_pct` ρ = −0.025 (t_NW −3.1, p 0.002 – not significant after multiple-testing correction; partly mechanical, calmer stocks touch the −2 % stop less often). Sector-relative momentum ρ ≈ +0.007 (t ≈ 1.8).
- **Large |t| only for short histories:** options IV (11 dates), fundamentals/ARK (~105 dates) → flagged `reliable=False`, not usable yet.
- **Models:** LASSO selects no feature; RF permutation importance ≤ 0.0004 → no exploitable linear or non-linear structure in the current features.
- **Lesson:** Step B (ML P(hit)) can only add value once features carry signal. Priority shifts to (1) letting short alt-data histories grow, (2) features for documented short-term effects (short-term reversal, PEAD/SUE, pre-earnings drift) and (3) new event sources (8-K buybacks, index additions) – each measured against this baseline.

### [2026-10-06] 📊 Short-Term Price Features (A2): First Hints, Still No Usable Edge
Nine `st_*` features (migration 034, backfill 621k rows in 199 s), analysis rerun on the barrier label (+1 %/−2 %/14d).
- **Best reliable features now come from A2:** `st_close_location` ρ = +0.011 (t_NW 3.5, p 0.0006 – close near the day's high → better trade outcome), `st_earnings_reaction` ρ = +0.009 (t 2.7, also t 2.5 on `return_1d` → post-earnings drift direction as in the literature), `st_dist_52w_high` ρ = +0.012 (t 1.9). None passes the multiple-testing correction.
- **Reversal effects** (`st_return_5d`, `st_rsi_2`) point the expected way on `return_5d` (ρ ≈ −0.01, t ≈ −1.7) but are not significant – weak in large caps.
- **Models:** LASSO still selects nothing; RF importance ≤ 0.00025.
- **Scale:** an IC of 0.01 for the top 5 of ~680 stocks means roughly IC × σ × z ≈ 0.01 × 1.5 % × 2.7 ≈ 0.04 % extra per trade – far below the +1 % target.
- **Lesson:** Literature effects are visible in direction but tiny in size. A combined model (step B) is the honest final test whether several weak signals add up to a hit rate clearly above break-even out of sample.

### [2026-10-06] 📊 Walk-Forward Hit Model (Step B): Failed – Features Cannot Rank Stocks
Pre-registered test ([concept §7.2/7.3](2026-10-06_Konzept_Kurzfrist_Kandidaten.md), [report](reports/2026-10-06_hit_model_walkforward.md)): P(hit) for +1 %/−2 %/14d via L2 logit and shallow HGB, quarterly expanding walk-forward with 16-session purge, rule chosen on 2023Q3–2024Q4, evaluated once on 2025Q1–2026Q3.
- **Daily AUC ≈ 0.50** in development and holdout for both models → combining ~90 weak features does not create ranking power.
- **Holdout (gbm, max 1/day, P ≥ 0.65):** 386 trades, hit rate 69.9 % vs. 68.3 % break-even, net +0.062 %/trade (conservative −0.039 %), difference to universe +0.075 % with 95 % CI [−0.047, +0.175] → not significant. Development looked better (+0.144 %) – typical selection optimism.
- **Calibration flat:** hit rate 65–70 % in every P decile; the threshold only selects calmer days/stocks, not winners.
- **Technical:** sklearn `HistGradientBoostingClassifier` crashes ("window shape cannot be larger than input array shape") on all-NaN or constant training columns → filter usable columns per fold.
- **Lesson:** With daily data and the current sources there is no validated edge for a +1 % short-term trade. Do not put Hyperopt on top of a zero signal. Keep the walk-forward script as the benchmark for every new source; any rerun needs a fresh holdout (from 2026-Q4) because the current one has been used.

### [2026-10-06] 📊 Event Studies (Step D): Buyback Authorizations Fail, Index Additions Hint at Weakness
Pre-registered ([concept §7.4/7.6](2026-10-06_Konzept_Kurzfrist_Kandidaten.md), [buyback report](reports/2026-10-06_event_study_buyback.md), [index report](reports/2026-10-06_event_study_index_add.md)).
- **D1 buybacks (8-K):** 648 labelled events (2022-06 … 2026-09), hit rate 66.2 %, net −0.063 %/trade, difference to same-day universe −0.069 % [−0.210, +0.048] → **failed**. With earnings in the same 8-K the conservative variant is significantly negative (entry falls into the high-volatility window). The literature's buyback drift is a multi-month effect, not a 14-day +1 % trade.
- **D2 index additions (exploratory):** entry after the effective date −0.32 % vs. universe (Nasdaq-100 worse than S&P 500), 108 events on 53 dates → only a hint for a warning flag.
- **Classifier:** regex/sentence rules on EDGAR full-text search hits reach ≈ 90 % precision (40-sample audit). Typical false positives: restated older authorizations in earnings releases ("as previously announced", "In May …"). The first date in the sentence decides staleness.
- **Technical:** EDGAR FTS (`efts.sec.gov/LATEST/search-index`) caps at 10k hits → monthly windows. `www.sec.gov/Archives` answers HTTP 429 during long download runs (~80 of 4.8k failed) → cache per document and rerun; caching candidate sentences (not verdicts) lets the classifier be re-run without downloads.
- **Lesson:** Event signals with a known long-horizon effect do not automatically carry over to a short +1 %/−2 % barrier trade. Test the horizon first, and only build collectors/tables after the study passes.

### [2026-09-08] 🛠️ Feature Groups Defined in Three Places Drift Apart
After migration 028 the new feature groups (macro, breadth, sector, liquidity, short interest, 13F, options IV, estimates) were computed and checked by the readiness script, but the features page still showed 9 groups: `api/routes/features.py` and `api/schemas.py` (`total_possible`) had their own hard-coded group lists (fixed in `4409caf`). **Lesson:** one source of truth for feature groups. Since the October review this is `analysis/feature_groups.group_features(FEATURE_COLUMNS)`, used by the API, `analysis/feature_report.py` and `scripts/sprint10_readiness.py`.

### [2026-09-08] 🛠️ After a Deploy the Browser May Show the Old SPA
The version label stayed at "Sprint 8" after a rebuild although the new image was running – the browser served the cached `index.html`. A hard reload (Ctrl+F5) fixed it. **Lesson:** check UI changes after a deploy only after a hard reload before debugging the build.

### [2026-10-11] 📊 First Data-Retention Run
Sunday 2026-10-11 03:00: cutoff `2021-10-01` (Q4 2026 − 20 quarters), 135,084 rows deleted in ~10 s (`prices_daily` 133,954, `macro_series` 943, `technical_indicators` 187). All other tables were already inside the window. The next cutoff move happens on 2027-01-01 (→ 2022-01-01).

### [2026-10-15] ⚠️ Dashboard Date Ranges Are Not Retention Dates
After the retention run the dashboard still showed `insider_trades` from 2019 and `form13f_holdings` from 2021-03-31. Not a bug: retention prunes these tables by `filing_date`, the dashboard shows `transaction_date` / `report_period` (insider event dates contain bogus years, 13F report periods precede the filing by ~45 days).

### [2026-10-15] 🛠️ Pydantic `date` Fields Reject Datetimes – and a Broad `except` Hides It
`news_articles` / `news_sentiment` appeared on the dashboard with `row_count = -1`. Cause: `TableStats.min_date/max_date` are `date`, but `published_at` / `scored_at` are timestamps; Pydantic v2 rejects datetimes with a non-zero time for `date` fields, and the per-table `except Exception` turned the validation error into `-1` (fixed in `050ff32` by normalising to `.date()`). **Lesson:** a catch-all that maps errors to a sentinel value needs at least a log line; otherwise type errors look like data problems.

### [2026-10-15] ⚠️ Switching to a Non-Root Container Breaks Writes to Old Host Files
After the switch to UID/GID `99:100` (2026-10-05) the context pack failed twice with `PermissionError` – existing folders/files on `/mnt/user/Workfiles/AlpacaBroker` had been created by root. Fixed on the host with `chown -R 99:100 /mnt/user/Workfiles/AlpacaBroker`. The matplotlib warning about `/app/.config` in `feature_analysis` had the same cause (fixed by `64089e2`). **Lesson:** when the runtime user changes, migrate ownership of mounted volumes at the same time; never run scripts with `docker exec -u root` (plain `docker exec` already uses `99:100`).

### [2026-10-15] 🛠️ Deadlock Between Target Backfill and a Concurrent Manual Writer
On 2026-10-05 12:10 `target_backfill` (manually triggered chain) failed with a PostgreSQL deadlock; no other scheduler job ran at that time, so the second writer was a manual script. The next nightly run repaired everything. Since `97fcdb0` the backfill retries on SQLSTATE 40P01. **Lesson:** idempotent batch writers should retry deadlocks; manual repair scripts that write `feature_snapshots` must not overlap with the nightly chain.

### [2026-10-15] 📊 `short_interest` Table Is Empty by Design (for Now)
`short_interest_collector` only writes `short_volume` (daily short volume via the Massive API). The `short_interest` table (bi-weekly settlement-date short interest, migration 027) exists but is not filled by any collector. The short-interest feature group is built from `short_volume` only.

**Update 2026-10-16:** no longer empty by design – the weekly `short_interest_positions_collector` now fills the table from Massive `GET /stocks/v1/short-interest` (free tier, history since 2017), and the group gains `short_interest_days_to_cover` / `short_interest_change_1m` (migration 036, 9-session publication lag). The table stays empty until the initial backfill ran after the deploy. Decision: [DECISIONS_DATA.md](DECISIONS_DATA.md) 2026-10-16.

### [2026-10-16] ⚠️ A Silent Fallback Hid Every S&P 500 Exit for Two Months
**Observation:** The point-in-time S&P 500 universe had 426 members in 2022 and 490 in 2026 instead of ~503; 92 real index exits inside the data window were missing (87 without any `index_membership` interval). Chain of causes ([inventory](reports/2026-10-16_sp500_survivorship_inventory.md)): Wikipedia moved the change table to "Historical components of the S&P 500"; the parser did not find it on the old page, and `8b7709e` (2026-08-28) had added a fallback to the "Date added" column of the current members – additions only, logged at INFO as "no removal data available"; a second latent bug (MultiIndex flattening dropped the `Added`/`Removed` group names) would have hidden the table even on the right page; the seed endpoint consulted the GitHub CSV only if Wikipedia returned fewer than 20 changes (the fallback's 80–90 additions passed), and that CSV ended in 2019; `IndexSyncer` can only close intervals that are open in the DB and stamped `date.today()`, so the first monthly run closed three intervals 10 days late.
**Lesson:**
- A fallback that returns *less information* must say so in its result type (`ParseResult.complete`), be logged at ERROR, and be refused by every writer that needs the missing part (the seed endpoint now answers 502). A silent fallback that is "good enough" for one consumer (additions) is wrong for another (survivorship).
- Check the *shape of the result*, not only that a job ran: the number of point-in-time S&P 500 members per day (~500–505) would have exposed the gap on day one. The rebuild script now blocks `--apply` when any session has a count outside 495–510; the monthly sync only checks the size of the current list (480–520).
- Renames are not exits, and tickers are re-used (FB, LB, PARA, INFO): key history by the current symbol and fetch prices with `asof` = first member day.
- Manual hygiene does not scale: 10 delisted/renamed tickers stayed active for months until `validate_universe.py` was run by hand; it is now the weekly job `universe_validation`.
- When a source is known to have changed (`8b7709e` noted that the table was gone), re-verify what the dependent data looks like instead of only making the parser "not crash".
**Status:** ✅ Solved 2026-10-17 – code fixed (`c290f65`, `3d85fcf`) and deployed, membership and prices repaired (steps 2a/2b), full feature rebuild finished 17:13 (676,953 snapshots), analyses re-run on the repaired data with unchanged conclusions (K1, B, D2; see the entry "Survivorship repair did not change the research conclusions" below), see [DECISIONS_DATA.md](DECISIONS_DATA.md) 2026-10-16 and 2026-10-17.

### [2026-10-17] ⚠️ A Wrong Yahoo Symbol Blacklisted a Current S&P 500 Member as a Fund (BF.B)
**Observation:** Brown-Forman Class B (BF.B), a current S&P 500 member, was `is_active = false` and in `ticker_blacklist` as `MUTUALFUND` / `manual_enrichment` since 2026-04-15. The sector enrichment asked Yahoo for the raw symbol `BF.B` before `to_yahoo_symbol` mapped it to `BF-B`; the lookup returned a fund and the self-learning ETF filter blacklisted and deactivated the ticker. BF.B never had a price row, yet 1,266 feature snapshots existed for it (all without price data). Nobody noticed for six months; it surfaced in the dry run of the survivorship repair, which checks the current index members against the universe. It was the only false positive among the 54 blacklist entries (the other 53 are ETFs).
**Lesson:**
- A learned filter that deactivates tickers needs a veto from stronger evidence: index membership proves an equity (`apply_sector_records`, `NewTickerOnboarder`), benchmark ETFs are never deactivated. Decision: [DECISIONS_DATA.md](DECISIONS_DATA.md) 2026-10-17.
- Symbol forms differ per source (`BF.B` in the universe, `BF-B` for Yahoo): map before every external lookup, in every code path, not only in the main one.
- Cross-check derived lists against an independent source: `select b.ticker from signals.ticker_blacklist b join signals.index_membership m on m.ticker = b.ticker where m.valid_to is null` must return no rows; a current member without prices is a data hole, not a quiet default.
**Status:** ✅ Fixed – BF.B reactivated and removed from the blacklist by the repair on 2026-10-17 (`8de96a1`, `d03955c`).

### [2026-10-17] 🛠️ `alpaca-ops run` Dies with the Popup – Long Runs Need `run-bg`
**Observation:** `alpaca-ops run` executes the script in the foreground of the User Script popup; closing the popup (or the browser session) ends the script. A full feature rebuild takes about 5.3 h for the feature step (1,266 sessions at about 15 s each).
**Lesson:** Use `alpaca-ops run-bg` for anything longer than a few minutes (detached, output in `ops/bg-job.log` with the exit code, `run-log` / `run-stop`). While a project script runs, `deploy` and `restart` refuse, and the detached run is not stopped at 22:15 – plan it so that it ends before the nightly chain. Decision: [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) 2026-10-17.
**Status:** ✅ Solved (`158bc59`).

### [2026-10-17] 🛠️ The SMB Share Showed a Stale Run Log – Read the Popup Text
**Observation:** `ops/run.log` on the share showed only its first 4 lines for minutes although the run had long finished (Windows caches share files; same effect as with `deploy-worker.log` on 2026-10-16). The mtime and the API were current.
**Lesson:** For live output, read the text of the popup's iframe through the browser pane (`document.querySelectorAll('iframe')[1].contentDocument.body.innerText`) instead of screenshots or the cached file; trust the API and `ops/deployed.txt` over any rewritten log. For detached runs use `alpaca-ops run-log`.
**Status:** ✅ Workaround documented (CLAUDE.md, "Unraid actions").

### [2026-10-17] 🛠️ Repair Scripts Have an Order – a Dry Run Before the Predecessor Is Not Informative
**Observation:** `onboard_removed_members.py` reads `index_membership` to decide whom to onboard. Its dry run *before* `rebuild_sp500_membership.py --apply` found nothing to onboard and printed the warning "run rebuild --apply first"; only the PARA part (independent of the membership) was meaningful. The real dry run after the rebuild produced the runbook numbers exactly (39,130 bars for the 68 exits).
**Lesson:** Follow the runbook order (rebuild, dry run, apply, dry run); read warnings of a dry run as a precondition check, not as noise. A dry run that "finds nothing" can mean the precondition is missing, not that nothing is wrong.
**Status:** ✅ Runbook order confirmed ([report](reports/2026-10-16_sp500_survivorship_inventory.md), section 8).

### [2026-10-17] 📊 Survivorship Repair Did Not Change the Research Conclusions
**Observation:** After the S&P 500 history was repaired (503–506 point-in-time members on every session, 68 exits onboarded, feature rebuild: 676,953 snapshots, +49,541 rows / +7.9 %) the analyses were re-run read-only on the repaired data ([event study](reports/2026-10-17_event_study_index_add.md), [walk-forward](reports/2026-10-17_hit_model_walkforward.md), [barrier backtest](reports/2026-10-17_barrier_backtest.md)):
- **D2 index additions:** 110 labelled events (before 108), difference to the universe −0.320 % [−0.524 … −0.277] (before −0.321 %) → unchanged, still negative.
- **Step B hit model:** 557,275 snapshots (before 515,412), Ø holdout AUC logit 0.511 / gbm 0.499 (before 0.510 / 0.509); the chosen rule changed to "gbm, max. 3/day, P ≥ 0.683"; holdout 954 trades, hit rate 67.8 %, net −0.006 %, difference to the universe +0.014 % [−0.126 … 0.158] n.s. → still not passed (before 386 trades, +0.075 % n.s.).
- **K1 barrier backtest of the context-pack score** (2022-07-28 … 2026-10-16, 1,060 days, 557,166 universe trades): no variant with a significantly positive net return; only "Top 5 filtered" beats the universe slightly (TP 0.5 %/SL 1.5 %: +0.026 % on the OHLC path, still net negative) → score ≈ random, as before.
- **Sprint 10 readiness:** 29/31, GO (same as on 2026-10-16); ML window 1,060 dates, 568,079 snapshots (before 532,264), 752 tickers (before 685).
**Lesson:** The survivor bias was real in the data (the point-in-time universe was wrong on 1,242 of 1,265 days), but removing it moved none of the conclusions, so it was not the reason for the null results. The conclusions "no usable edge in daily-bar features for a +1 %/−2 % trade" rest on the repaired data now. Keep the pre-repair reports (2026-10-06) as the comparison and re-run the analyses after every change of the universe or target definition. The monthly `feature_analysis` was re-run on the repaired data later the same day (entry below): still no significant feature.
**Status:** ✅ Confirmed 2026-10-17.

### [2026-10-17] ⚠️ No Delisting Returns – Targets of Abruptly Delisted Tickers Are NULL
**Observation:** For tickers that vanish abruptly (SIVB, FRC), no price exists after the last trading day, so the forward targets whose horizon reaches past it are `NULL`: roughly the last 20 sessions before the delisting have no `return_20d` (shorter horizons fewer). The worst outcomes (down to −100 %) therefore never enter targets, walk-forward tests or backtests. The survivorship repair made these tickers visible in the universe (the exits now have snapshots up to their last member day), but not their final loss.
**Lesson:** Results on the repaired data are still slightly optimistic for tail risk: a model or filter that would have bought SIVB in the weeks before its collapse is not penalised. Possible fix later (not urgent): a CRSP-style delisting return from the last trade / corporate action, or labelling such trades with the last available price. Until then, read tail-risk statistics (worst trades, maximum drawdown) of backtests as optimistic.
**Status:** 🟡 Known limitation, backlog ([ROADMAP.md](ROADMAP.md)).

### [2026-10-17] 🎯 First Short-Interest Evaluation: Days-to-Cover Has a Consistently Negative IC
**Observation:** The monthly `feature_analysis` was re-run on the repaired data on 2026-10-17 (report `2026-10-17`: 568,079 snapshots, 752 tickers, 2022-07-28 … 2026-10-16, 87 features in the correlations, RF importance 86 features). As in every report so far: no feature is significant (Bonferroni over all feature×target tests *and* enough independent periods), LASSO selects nothing, hypotheses unchanged (H1–H4, H9, H12 `insufficient_data`; H10, H13 `inconclusive`). The consensus top 10 changed again (now `st_return_1d`, `st_close_location`, `volume_ratio_20d`, `short_interest_days_to_cover`, `st_rsi_2`, `days_since_last_earnings`, `price_vs_sma200`, `st_gap`, `earnings_days_until`, `insider_buy_ratio_90d`; on 2026-10-06: `st_dist_52w_high`, `st_return_1d`, `days_since_last_earnings`, `st_gap`, `earnings_days_until`, `sue_last`, `relative_strength_spy`, `st_bollinger_pctb`, `cluster_count_30d`, `form13f_top_holder_count`) – the ranking is still unstable (readiness check: 0/10 overlap).
**First evaluation of the FINRA short interest** (planned for the 2026-11-01 report, available early): `short_interest_days_to_cover` has a **negative** daily rank IC at every horizon – `return_5d` rho −0.016 (t_NW −3.32, p 0.0009), `return_20d` rho −0.029 (ICIR −0.35, t_NW −3.20, p 0.0014), `return_60d` rho −0.044 (ICIR −0.58, t_NW −3.40, p 0.0007), `return_barrier_14d` t_NW −1.85; 1,000–1,059 dates, so the estimate is reliable, but it does not survive the Bonferroni correction (~435 tests). The direction matches the literature (high days-to-cover → later underperformance). `short_interest_change_1m`: nothing (|t_NW| < 0.7). It is #4 in the consensus ranking.
**Lesson:**
- `short_interest_days_to_cover` is the first new-source feature with a consistent, long-history sign across horizons – a candidate for the F1 composite score or a pre-registered test, **not** a confirmed signal (not significant after the correction, effect size small).
- Very large t-values of young sources are **not reliable**: `eps_revision_pct_90d` t 10.0, `eps_revisions_net_30d` t 9.9, `short_volume_ratio_5d` / `_20d` t −8.8 / −8.6 (all `return_20d`), `options_iv_term_slope` t 7.3 (`return_barrier_14d`) rest on 14–20 dates (Estimates 53 days of history, Options IV and Short Volume 34) – the 20-session targets overlap, so there are too few independent periods (`reliable: false` in the report). Do not act on them; watch them in the next monthly reports as the history grows.
- Because every date up to 2026-10-16 has now been looked at, the confirmatory test uses only later data and was **pre-registered as H14** the same day (fixed n = first 250 out-of-sample dates from 2026-10-19, one-sided, single look, verdict expected with the 2027-12-01 report; power only ≈ 45–50 %) – see [LEARNINGS_HYPOTHESES.md](LEARNINGS_HYPOTHESES.md) H14 and [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md) 2026-10-17.
**Status:** 🟡 Open – watch `short_interest_days_to_cover` and the young sources in the 2026-11-01 and 2026-12-01 reports.

### [2026-10-17] 🧭 Ask for the Scope Before Encoding Strategy Rules into Targets
**Observation:** After the monthly analysis showed hints at the 60-day horizon, a trailing-stop trend-trade label (`return_trail_60d`, migration 037, 3 × ATR(14) trail, 60 sessions) was designed, implemented, tested, deployed and made the model target within one evening (`608674c`, 1,187 inserted lines). Sebastian then clarified that Alpaca-Broker only forecasts which tickers rise (later fall) and hands the data to Claude via the context pack; strategies and exit rules belong to a separate bot. The label (and, before it, the 14-day barrier label) encoded exactly the strategy that this project is not supposed to own. The label was removed again the same evening (migration 038); only the strategy-neutral `return_60d` stays as model target.
**Lesson:** A target that contains entry/exit rules (barrier, trailing stop, time stop, costs) is a strategy, not a forecast. Before adding one, ask whether the project owns the strategy; for a forecast use plain forward returns (`return_5d` / `return_20d` / `return_60d`). Applied to an already applied migration this costs a second migration (add 037, drop 038) – cheaper to ask first. The 14-day barrier label, the context pack and stage 2 still carry the older trade concept and are to be made neutral in a planned step ([DECISIONS_FEATURES](DECISIONS_FEATURES.md) 2026-10-17).
**Status:** ✅ Trailing label removed (migration 038, deployed 2026-10-17 19:26); ✅ context pack and stage 2 reworked neutrally (deployed 2026-10-18); 🟡 barrier label removal open (plan step 6, migration 041).

### [2026-10-18] 🛠️ An Explicit `ck_…` Name in a Migration Is Expanded Twice by the Naming Convention
**Observation:** The Alembic metadata (`env.py`, `target_metadata = Base.metadata`) carries the naming convention `ck_%(table_name)s_%(constraint_name)s`. A `CheckConstraint(..., name="ck_forecasts_direction")` in `op.create_table` therefore became `ck_forecasts_ck_forecasts_direction` on a fresh database (and a long one was truncated to `…_sam_a296`), while the ORM model with `name="direction"` expects `ck_forecasts_direction`. Migration 035 has the same effect (`ck_stage2_decisions_ck_stage2_decisions_action` in production). `alembic check` did not notice: autogenerate does not compare CHECK constraints.
**Lesson:** In a migration give CHECK constraints the short name (`name="direction"`), like the ORM; the convention expands it. Do not rely on `alembic check` for CHECK constraints: `tests/unit/test_migration_039.py` builds the tables from the migration's `op.create_table` calls (with the convention) and compares the DDL with the ORM tables. Unique constraints with an explicit name are used as given (the `uq` convention has no `%(constraint_name)s`).
**Status:** ✅ 039 uses the short names; 035 is untouched (its tables are dropped by 039).

### [2026-10-18] 🛠️ Rebuilding the Gate of Every Forecast Session Must Not Run the Whole Screen
**Observation:** The evaluation needs the quality-gate flag of each forecast session (the `screen.csv` population). Running `screen_dates` (percentile ranks, group scores, buckets) for 330 sessions x 60 tickers took 13 s in the nightly step; with ~650 tickers and a growing number of sessions it would take minutes.
**Lesson:** `forecast_review.gate_flags` uses only the gate part of the screen (feature values, groups with data, `quality_gate`): 3.6 s for the same data, and a test pins its equality with `screen_cross_section`'s `gate_ok` (so the two cannot drift apart).
**Status:** ✅ implemented.

### [2026-10-18] 🐛 EDGAR Lists Multi-Party Filings (13D/13G/144) Under Filer AND Subject
**Observation:** After the 120-day backfill `signals.sec_filings` held 650 `SCHEDULE 13G(/A)` rows for BLK, 162 for STT and dozens each for MS/GS/JPM - e.g. JPM rows with accession prefix `0000019617` (JPM's own CIK) whose URL is `.../xslSCHEDULE_13G_X02/primary_doc.xml`: stakes JPM holds in OTHER companies, attributed to JPM. Cause: EDGAR lists a Schedule 13D/13G or a Form 144 in the submissions JSON of every party - the filer / holder (the bank filing a 13G about another company, the insider selling) AND the subject company (the issuer the stake or sale is about). The collector stored whichever copy it met first (the unique accession number silently dropped the other: ~200 subject copies were lost), and the context pack would have shown a 13G under BLK instead of under the company it is about. The submissions JSON carries no role (both lists show the same accession, form and document); the SEC header of the filing does: `<SUBJECT-COMPANY>` (issuer) and `<FILED-BY>` / `<REPORTING-OWNER>`.
**Fix:** a multi-party filing is stored only under the CIK of the `SUBJECT COMPANY` block of its SEC header (`<accession>.hdr.sgml`, one 1-5 KB request, fallback: head of `<accession>.txt`), with that CIK's tickers and URL; a subject outside the universe is not stored; one lookup per accession and run (the subject's copy wins whatever list is read first); a failed lookup skips only that filing (retried by the next runs). `scripts/repair/fix_sec_filing_subjects.py` re-attributes (update) or removes (delete) the rows already stored.
**Lesson:**
- **An accession-number prefix is the CIK that SUBMITTED a filing, not the filer of record.** The tempting shortcut "prefix == listing company -> the company is the filer, skip" is wrong: Walmart submits the Form 144s of its own executives under its own login (17 of 158 listed 144s have the prefix `0000104169`; the header shows Walmart as `SUBJECT-COMPANY`), and a company may submit a plan trust's or a founder's schedule about itself. Where the prefix does match a filer (JPM, NVIDIA, Walmart's own 13Ds about Symbotic) the header is still needed to tell which. Checked 2026-10-18 on 6 headers (13G/A of JPM, Vanguard and Berkshire, Walmart's 13D/A, the 144s of Walmart and Apple) and on the 144 / 13D/G lists of META, NVDA, MSFT, WMT.
- Whenever a feed lists "everything a company appears in" (submissions JSON, 13F, Form 4), check for filings where the company is only the filer before attributing rows to a ticker.
- Cost: one extra small request per not-yet-stored multi-party accession (~45 on a normal day) plus the re-reads of filer copies that are never stored (BlackRock & co., up to 14 lookback days) - a few hundred requests, at most a few minutes.
**Status:** ✅ Collector fixed; 🟡 repair of the stored rows pending (dry run first, `--apply` needs Sebastian's OK).

### [2026-10-18] ⚠️ A Second Writer on the Share Needs World-Writable Pack Folders – Group Write Is Not Verifiable Through virtiofs
**Observation:** The forecast VM writes `forecasts.yaml` into the pack folders over a virtiofs mapping of `/mnt/user/Workfiles/AlpacaBroker`; the folders belong to the container user `99:100` and were set to `2775` (group write, the VM user is in gid 100). From the VM, writing as "other" was verified (the VM user creates files as `1000:1000`, mode `664`). Group write through the supplementary group 100 could not be verified: virtiofsd switches to the caller's uid/gid but may drop the supplementary groups, so the VM may see itself as "other" only.
**Lesson:** For a second writer on an Unraid share through virtiofs, rely only on permission paths that were actually tested from the guest. A forecast that cannot be written is a missed forecast, so the generator now sets `2777` (`PACK_DIR_MODE`, setgid kept); the integrity of the file comes from the seal, not from permissions ([DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) 2026-10-18). Files created by the VM stay `1000:1000`/`664`, so the container can read but not change them.
**Status:** ✅ `2777` set in code (uncommitted on the evening of 2026-10-18, goes live with the next deploy); 🟡 group write via gid 100 not verified, `2775` can return if it is.
