# LEARNINGS.md – Observed Findings & Data Quality

> Living document. Grows as we collect and analyze data.
> For hypotheses and planned investigations, see [LEARNINGS_HYPOTHESES.md](LEARNINGS_HYPOTHESES.md).
>
> See also: [INDEX.md](INDEX.md)

**Last updated:** May 2026

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
