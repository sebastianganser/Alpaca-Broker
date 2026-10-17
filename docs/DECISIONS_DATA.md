# DECISIONS_DATA.md – Data Source & Collector Decisions

> Decisions related to data sources, collectors, data quality, and universe management.
>
> See also: [INDEX.md](INDEX.md) · Related: [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) · [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md)

**Last updated:** 17 October 2026

---

### [2026-04-11] Data Strategy: Collect Maximally, Filter Later

**Context:** Which data sources to include? Risk of "signal overload" blocking every trade.

**Decision:** Collect maximally, use feature-selection algorithms to find relevant signals later.

**Rationale:** Feature-selection methods (correlation, LASSO, Random Forest) solve overload data-driven. Storage is cheap, retroactive data collection is impossible.

---

### [2026-04-11] Universe: Dynamically Growing with Signals

**Decision:** Start universe from ARK tickers + S&P 100, then organic growth through new signals.

**Rationale:** Avoids overhead from tickers without signals. Universe automatically reflects where smart money is active.

**Revisit trigger:** If important tickers systematically missing.

---

### [2026-04-12] Organic Data Growth (No Signal Backfill)

**Decision:** No backfill for signal data. All sources start from the same point and grow synchronously. *Exception: Price backfill from 2021 (see DECISIONS_FEATURES).*

**Rationale:** Synchronous data basis across all sources. Features like SMA 200 only activate when 200 days of data exist. Prevents "pseudo-alpha" from backfill data unavailable for other sources.

---

### [2026-04-12] Dynamic Feature Activation

**Decision:** Each derived feature defines a `min_data_days` threshold. Features only written to feature store when enough data exists.

**Rationale:** Prevents faulty signals from incomplete data. Features "self-activate" when the data basis is sufficient.

---

### [2026-04-12] Gap Extrapolation with Forward-Fill

**Decision:** 3-step process: (1) detect gaps via NYSE calendar, (2) reload from source, (3) forward-fill with `is_extrapolated=TRUE` flag.

**Rationale:** Forward-fill (last close as OHLC, volume=0) is the most conservative approach – creates no false signal. Flag enables downstream filtering.

---

### [2026-04-12] Alpaca as Source of Truth for Tradability

**Decision:** Alpaca Assets API (`GET /v2/assets`) is the authoritative source for whether a ticker is tradable. Non-tradable tickers are deactivated in universe.

**Rationale:** We can only trade what Alpaca offers. Collecting data for non-tradable tickers would be wasted effort.

---

### [2026-04-12] arkfunds.io API Instead of ARK CSV Scraping

**Decision:** arkfunds.io JSON API for ARK holdings. Endpoint: `GET /api/v2/etf/holdings?symbol={ETF}`.

**Rationale:** Clean JSON vs. fragile CSV parsing. Free, no auth needed, Swagger docs. Includes `share_price` and `weight_rank`. Downside: third-party dependency.

---

### [2026-04-12] Alpaca Market Data API Instead of yfinance (for Prices)

**Decision:** `PriceCollectorAlpaca` replaces yfinance as primary price source. Multi-symbol endpoint with `adjustment=all` and `feed=iex`. yfinance code kept as fallback.

**Rationale:** Official API, stable, price consistency with trading platform. Batch endpoint: 100 tickers/request, 644 tickers in 7 requests (<10s).

---

### [2026-04-12] Universe Expansion to S&P 500 + Nasdaq 100

**Decision:** Universe contains S&P 500 + Nasdaq 100 + ARK additions. Wikipedia as source for index lists.

**Result:** 644 active tickers. ~80% of ARK tickers already covered.

---

### [2026-04-13] SEC EDGAR Access Strategy: Submissions API + XML Parsing

**Decision:** Submissions API + stdlib `xml.etree.ElementTree`. No new packages.

**Rationale:** Submissions API is more stable than EFTS, delivers accession numbers + primary documents directly. stdlib XML parsing – no additional dependency.

---

### [2026-04-13] Form 4: Universe-driven (not Global Search)

**Decision:** Only Form 4 for our active tickers (universe-driven with auto-expansion).

**Rationale:** At 644 tickers and ~10 req/s, takes ~65 seconds – acceptable for a daily job. Global search would generate massively more data and SEC requests.

---

### [2026-04-13] Form 13F: Filer-driven (Top-20 Institutional)

**Decision:** Top-20 hand-picked filers (Buffett, Burry, Ackman, Renaissance, Tiger, Bridgewater, Citadel, Two Sigma, D.E. Shaw, Millennium, Point72, Greenlight, Baupost, Third Point, Icahn, Elliott, Duquesne, Coatue, Appaloosa, ARK).

**Rationale:** 13F has 45-day delay anyway – context data, not tactical signal. The top-20 known "smart money" investors are the most interesting.

---

### [2026-04-13] Politician Trades: Senate eFD (Free) Instead of Quiver API

**Decision:** Scrape Senate eFD directly (free, official government source).

**Rationale:** Constraint "stay free" excludes Quiver ($30/mo). Senate eFD is the official primary source. House Clerk deferred (PTRs are PDF – requires PDF parsing).

---

### [2026-04-13] Politician Trades: Weekly Schedule (not Daily)

**Decision:** Weekly Sunday 11:00 CET.

**Rationale:** STOCK Act allows politicians up to 45 days filing deadline. Daily scraping would be wasteful.

---

### [2026-04-13] Politician Trades: No Universe Auto-Expand

**Decision:** Trades stored (ticker field), but universe unchanged. *(Later overridden: NewTickerOnboarder now auto-expands for politician trades too – see DECISIONS_ARCHITECTURE.)*

---

### [2026-04-13] House PTRs: Deferred to Future Sprint

**Decision:** Sprint 4 implements only Senate eFD. House PTRs are a future enhancement (PDF parsing complexity).

---

### [2026-04-13] yfinance for Fundamentals/Ratings/Earnings (Sprint 5)

**Decision:** yfinance for all three data types.

**Rationale:** Free (project constraint), already a dependency, delivers all needed fields. Risk (unofficial API) mitigated by graceful error handling.

**Revisit trigger:** If yfinance fails >2 weeks consecutively → migrate to FMP.

---

### [2026-04-13] UPSERT for Fundamentals/Earnings, DO NOTHING for Ratings

**Decision:**
- `FundamentalsSnapshot`: `ON CONFLICT DO UPDATE` (values change)
- `EarningsCalendar`: `ON CONFLICT DO UPDATE` (eps_actual arrives post-earnings)
- `AnalystRating`: `ON CONFLICT DO NOTHING` (unique events, dedup only)

---

### [2026-04-13] YFinanceClient as Shared Infrastructure

**Decision:** Common `YFinanceClient` instead of code duplication across 3 collectors.

**Rationale:** Avoids triple implementation of rate-limiting (0.5s/ticker, 3s/batch), batch iteration, graceful error handling.

---

### [2026-04-13] Night Slot 01:00–03:00 CET for yfinance Jobs

**Decision:** All yfinance jobs in the night slot.

**Rationale:** 2-hour window gives buffer for rate-limit retries. Yahoo servers less loaded at night (19:00 ET). No collision with existing daily jobs (22:15–00:00).

---

### [2026-04-13] `upgrades_downgrades` Instead of `recommendations_summary`

**Decision:** Use `upgrades_downgrades` for individual firm-level entries.

**Rationale:** More granularity – we know *which* firm upgraded/downgraded and when. More valuable for feature calculation.

---

### [2026-04-13] `eps_growth_yoy` from `get_earnings_estimate()`

**Decision:** Use Yahoo's pre-calculated value instead of own calculation.

**Rationale:** Simpler, less code. Own calculation error-prone with split adjustments and fiscal year differences.

---

### [2026-04-14] SEC Form 4: XSLT Prefix Stripping

**Decision:** Strip XSLT prefix `xslF345X06/` from `primaryDocument` path.

**Rationale:** SEC renders XSLT transformations on-the-fly but only stores raw XML files. Prefix is a virtual path for the HTML view.

---

### [2026-04-14] SEC Form 4: Company-CIK for Archive URLs

**Decision:** Use Company-CIK (from `company_tickers.json`) for downloads, not Filer-CIK from accession number.

**Rationale:** SEC stores filings under the subject company directory, not the filer (agent).

---

### [2026-04-14] Senate eFD: curl_cffi Instead of Python requests

**Decision:** `curl_cffi.requests.Session(impersonate="chrome131")`.

**Rationale:** Drop-in replacement with Chrome TLS fingerprint. Minimal code change. Senate eFD blocks Python requests via JA3 hash detection.

---

### [2026-04-14] Senate eFD: DataTables AJAX Endpoint

**Decision:** Call DataTables AJAX endpoint directly (`POST /search/report/data/`) instead of HTML parsing.

**Rationale:** AJAX delivers structured JSON. Session flow: Agreement → Search-Form POST → AJAX data.

---

### [2026-04-15] Dividend Yield: Backend Normalization

**Context:** yfinance delivers `dividendYield` in percent form (0.4 = 0.4%), while all other ratio fields come as decimal (0.451 = 45.1%).

**Decision:** Backend normalization (`/100` at storage) + data migration 013.

**Rationale:** Consistent data storage. All percent fields stored as decimal in DB.

---

### [2026-04-15] Plausibility Checks for Fundamentals

**Decision:** `_validate_fundamentals()` checks all 17 fields against defined plausible ranges.

**Update [2026-04-16]:** Ranges massively widened after 138 warnings on first production run – all values were real (negative P/B from buybacks, negative forward P/E from expected losses). **New philosophy:** Ranges = format guard (data corruption), not value filter. Only exception: `dividend_yield [0, 0.25]` stays tight as regression guard.

---

### [2026-04-16] Robust 13F Infotable Detection: 4-Stage Strategy

**Context:** 6 of 20 top filers delivered 0 holdings (Berkshire: `50240.xml`, Renaissance: `*_holding.xml`, etc.).

**Decision:** 4-stage detection: `infotable` → `informationtable` → `holding` → largest non-primary XML.

**Result:** 20,763 → 34,133 holdings (+64%). Berkshire Hathaway now captured correctly.

---

### [2026-04-16] yfinance Logger Set to CRITICAL

**Decision:** `logging.getLogger("yfinance").setLevel(logging.CRITICAL)` – suppress expected ERRORs (e.g., "No earnings dates found" for BRK.B).

**Rationale:** yfinance's ERROR level is too aggressive. Our own error handling is more differentiated.

---

### [2026-04-15] ARK Deltas: Only Real Portfolio Movements

**Decision:** `unchanged` positions skipped. Only `new_position`, `closed`, `increased`, `decreased` stored. API additionally filters `WHERE delta_type != 'unchanged'`.

**Rationale:** Portfolio movements are the signal, not the complete position list.

---

### [2026-04-15] Self-Learning ETF Blacklist System

**Decision:** yfinance `quoteType` as authoritative check + self-learning blacklist table (`signals.ticker_blacklist`).

**Rationale:** `quoteType` is the only reliable source. Detected non-equities permanently stored → O(1) lookup. Blacklist grows organically. Deactivated tickers kept in DB (never-delete policy).

---

### [2026-04-28] Benchmark Ticker Protection (SPY, QQQ)

**Context:** Automatic ETF filter deactivated SPY → 13 days of daily warnings and NULL values for relative_strength_spy.

**Decision:** `BENCHMARK_TICKERS = {"SPY", "QQQ"}` as protection set in `universe/blacklist.py`.

**Rationale:** Central, declarative protection. `add_to_blacklist()` rejects protected tickers. Extensible for new benchmarks.

---

### [2026-04-28] Politician Trades: disclosure_date as Primary Sort

**Decision:** `disclosure_date` as primary sort criterion + both dates in UI + `delay_days` calculation.

**Rationale:** For the investor, only the disclosure date is relevant. `delay_days` serves as signal quality filter with color coding (≤7d green, ≤30d yellow, >30d red).

---

### [2026-04-30] TA Catchup: Completeness Check (not just MAX)

**Context:** TA job reported "up-to-date" despite 0 records for 670 tickers on that date.

**Decision:** Two-phase detection: Phase 1 finds completely new days (after MAX), Phase 2 scans last 5 trading days for incomplete coverage (<90% → recomputation).

**Rationale:** MAX() only checks existence of at least one record, not completeness. Coverage comparison costs 2 extra queries per run, prevents multi-day invisible gaps.

---

### [2026-04-16] Insider Clusters: UniqueConstraint + UPSERT

**Context:** `InsiderCluster` was duplicated on every scheduler run – no UniqueConstraint, so PostgreSQL never detected conflicts.

**Decision:** `UniqueConstraint('ticker', 'cluster_start')` + `on_conflict_do_update`. Migration 017 deduplicates existing data.

**Rationale:** Idempotent – runnable any number of times without side effects. Clusters can evolve (new trades) → UPDATE is correct.

---

### [2026-05-02] Universal DATA_START_DATE for All Data Sources

**Context:** After historical backfill of insider trades, data contained outliers dating back to year 0024 (SEC filing typos) and forward to 2033 (vesting schedules). Different backfill scripts used different start dates. No single source of truth for "how far back should data go?"

**Decision:** Universal `DATA_START_DATE = date(2021, 1, 1)` in `trading_signals/config.py`. All data before this date is considered irrelevant or an outlier. All backfills, queries, and cleanup scripts reference this constant.

**Rationale:** Consistent data boundary across all modules. Aligns with the prices_daily backfill starting point. Prevents date outliers from distorting aggregations and dashboard displays.

**Future:** Retention window will become dynamic once ML determines the optimal lookback period.

---

### [2026-05-02] Form 4 Historical Backfill: Resume-Safe with Deep-Check

**Context:** Daily Form4Collector uses a 7-day lookback. For Sprint 8, we need ~3 years of historical insider trades. First backfill was interrupted by a Windows update (SSH session lost). Second run needed to distinguish "fully backfilled" tickers from those with only recent daily-collector data.

**Decision:** Two-phase approach:
1. `backfill_form4.py` with resume logic: skip tickers where `MIN(filing_date) ≤ 2023-12-31` (deep data present).
2. `cleanup_insider_outliers.py` post-backfill: delete trades outside `DATA_START_DATE..today`.

**Result:** 313,544 clean trades across 647 tickers (96% universe coverage). 392 outlier records removed.

**Lessons:**
- Atomic DB commits per ticker ensure no corrupt partial data after interruptions.
- Resume logic must check *depth*, not just *existence* of data.
- Always run backfills in `tmux` or with `nohup` to survive SSH disconnects.

---

### [2026-05-05] Sprint 8 Readiness Check: Depth + Coverage Validation

**Context:** The original readiness script only checked row counts (`COUNT(*) > 500`). It failed to detect that 50% of tickers had only 7 days of insider trade data instead of 3 years.

**Decision:** Rewrite `sprint8_readiness.py` with two new dimensions:
- **Depth:** Does `MIN(date)` reach back to the expected start date?
- **Coverage:** What percentage of active universe tickers have data in each table?

**Rationale:** Row counts alone hide distribution problems. A single ticker with 500 trades looks healthy in aggregate but masks 640 tickers with zero trades. Coverage + depth catches both volume and distribution gaps.

---

### [2026-08-18] Survivorship Bias and Lookahead: Rebuild the History Instead of Accepting It

**Context:** The data-extension concept ([2026-08-18_Konzept_Datenerweiterung.md](2026-08-18_Konzept_Datenerweiterung.md), §2) found that `universe` only held the current state (`is_active`, `index_membership` as a plain array, no `valid_from`/`valid_to`), so a model trained on today's index members sees only survivors. Checked against the code at the time: prices were already fetched with `adjustment=all` (no split/dividend distortion); `analyst_ratings` held ratings and price targets only, no EPS consensus (the estimates collector was a real gap, not a duplicate).
**Decision (Sebastian):** Clean up the history instead of living with the bias: reconstruct index membership from the publicly documented index changes, backfill delisted tickers, and base every point-in-time feature on filing/disclosure dates. The rebuilt history was to be loaded into temporary staging tables first, checked for completeness, and only then migrated into the live tables.
**Outcome:** Implemented in Sprint 9.5a (A1/A2) as the `index_membership` interval table (migration 023), `get_universe_as_of()`, a delisted-ticker backfill and a feature recompute – see ROADMAP. No staging tables exist in the schema. Lookahead rules: see "Information Cut-offs" in DECISIONS_FEATURES.
**Correction 2026-10-16:** The S&P 500 part of this outcome was incomplete. `index_membership` contained only the members that were current at seeding plus dated additions – almost no S&P 500 exit (92 real exits inside the data window missing, point-in-time universe 426 instead of ~503 members in 2022), because the parser silently fell back to an additions-only source. The Nasdaq-100 history already contained its exits (56). Cause, convention and repair: [2026-10-16] S&P 500 Membership History (below).

---

### [2026-08-18] Free Sources for Estimates and Short Interest, Strictly Throttled

**Decision (Sebastian):** Use the free interfaces – `yfinance` for EPS/revenue estimates, revisions and earnings surprise (SUE), Massive (formerly Polygon) / FINRA for short interest. The blocking risk of these unofficial or rate-limited sources is handled by strict throttling (delays between calls, shared `YFinanceClient`) and error handling with exponential backoff, not by buying a licensed feed. The estimates collector went first because Yahoo's EPS-trend window rolls over 90 days – every missed day loses history for good.
**Alternative noted, not adopted:** licensed EODHD `calendar/trends` (same data model, ~50 USD/month, concept §3.1).

---

### [2026-09-22] Global Data Retention: 20 Calendar Quarters (≈ 5 Years), Quarter-Based Cutoff

**Context:** The warehouse had grown to ~2.7 M rows; some tables reached back further than 5 years (insider trades to 2019, prices/TA to late 2020, earnings calendar to 1999). Sebastian wanted one global maximum data age instead of per-table rules.

**Decision (Sebastian, 2026-09-22, commit `a2978cf`):**
- Keep at most **20 calendar quarters**. The cutoff is the **start of the quarter** that lies 20 quarters back (`utils/retention.quarter_cutoff()`), not "today − 5 years". Data older than the cutoff is deleted when a new quarter starts (the weekly job, Sun 03:00, picks up the moved cutoff).
- **`earnings_calendar` is excluded**: small (~19k rows over 27 years) and historically valuable (earnings history per ticker).
- `collection_log` keeps its own 90-day `log_retention`.

**Refinement (Code review, 2026-10-05):** Retention became the single source of truth for every start date: `data_start_date()` replaces the former fixed `DATA_START_DATE = 2021-01-01`; backfills must never re-collect data older than the cutoff; **no database backups** are kept (see CLAUDE.md, principle 2). Each table is deleted in its own savepoint; insider trades and 13F holdings are pruned by `filing_date` (their event dates contain bogus years), politician trades by `disclosure_date`, news sentiment via its article.

**Consequence:** The dashboard shows `insider_trades.transaction_date` / `form13f_holdings.report_period` as date range, so these tables can legitimately display dates older than the cutoff.

**Supersedes:** [2026-05-02] Universal DATA_START_DATE.

---

### [2026-10-01] Sprint 10 Readiness: Realistic Thresholds for Sparse Signal Groups

**Context:** `scripts/sprint10_readiness.py` failed the feature-coverage check for ARK (7.1 % vs. 15 %), politician (8.5 % vs. 20 %) and 13F (0 % vs. 10 %). These groups are structurally sparse: ARK holds only a few dozen of ~750 universe tickers, few tickers are traded by senators, 13F is quarterly with ~45-day lag.

**Decision (commit `a008047`):** Thresholds lowered to ARK 3 %, politician 5 %, 13F 5 %. Also fixed: the FRED maturity check queried the non-existent table `fred_observations` (correct: `macro_series`).

**Result 2026-10-01:** 26/30 checks passed. Remaining failures were time-dependent (total snapshots, options IV / short volume / estimates history) plus 13F (0 %, fixed later by the CUSIP→ticker resolver, 2026-10-05) and ranking stability across monthly analysis reports (40 % top-10 overlap).

---

### [2026-10-16] FINRA Short Interest via Massive (Settlement Data)

**Context:** The `short_interest` table (migration 027) had been empty since Sprint 9.5c: `short_interest_collector` writes only the daily short *volume*. ROADMAP carried the open point "collect FINRA short interest or leave the table empty on purpose". A probe on 2026-10-16 showed that `GET /stocks/v1/short-interest` works on the free tier, reaches back to 2017-12-29 and returns **all** tickers of one settlement date in a single page (details: [DATA_SOURCES.md](DATA_SOURCES.md) §10).

**Decision (Sebastian):** Collect it – chosen over leaving the table empty or dropping it – because the free tier offers the full history and needs only one request per settlement date (initial backfill ≈ 120 requests, ~25 min; afterwards ~3 requests per week). Implementation:
- Weekly job `short_interest_positions_collector` (Sunday 12:00 Europe/Berlin, `collection_log` name `short_interest_positions`), upsert on `(ticker, settlement_date, source)`; fetches new dates, the 2 most recent stored dates (FINRA revisions) and dates with < 50 % of the median row count; never before `data_start_date()`. Not a nightly upstream.
- Features `short_interest_days_to_cover` and `short_interest_change_1m` (migration 036, `FEATURE_VERSION` 2026.10-3, group "Short Interest").
- **Availability lag:** the payload carries no publication date, so a settlement date counts as public for day `d` only if it is <= the 9th NYSE session before `d` (FINRA publishes ~7 business days after settlement + 2 sessions margin); newest usable settlement older than 40 days → NULL. Rules: `derived/short_interest_features.py`, recorded in "Information Cut-offs" ([DECISIONS_FEATURES.md](DECISIONS_FEATURES.md)).
- **Evaluation first:** the features are judged in the monthly feature analysis before any score use. Expected value is mainly as a risk filter / for longer horizons, **not** a +1 %/14-day edge (see LEARNINGS 2026-10-06: no validated edge for the short-term barrier trade).

**Rationale:** Cheap to collect (one request per settlement date, free tier), point-in-time safe through the fixed lag, and the whole retention window (≈ 5 years) is available as history. The universe filter includes inactive tickers (survivorship).

**Consequence:** The history is back-filled for the whole retention window (`scripts/repair/collect_short_interest.py`, then `scripts/repair/backfill_short_interest_features.py --apply`), unlike the "organic growth" rule of 2026-04-12 – acceptable here because availability is derived from the settlement date and the fixed lag, so the backfilled feature rows are free of lookahead. Backfilled rows keep their old `feature_version`.

---

### [2026-10-16] S&P 500 Membership History: Removals, Rename Convention, Repair

**Context:** The survivorship-bias fix of 2026-08-18 (A1, above) turned out to be incomplete for the S&P 500. Inventory of 2026-10-16 ([report](reports/2026-10-16_sp500_survivorship_inventory.md), [removals CSV](reports/2026-10-16_sp500_removals.csv)): `index_membership` held for `sp500` only the 416 members that were current at seeding (`initial_seed`, from 2021-01-01) + 90 dated additions + 3 closures → the point-in-time universe had 426 (2022) … 490 (2026) S&P 500 members instead of ~503; of 92 real exits inside the data window (>= 2021-10-01) 87 had no interval at all; the PIT set differed from the truth on 1,242 of 1,265 trading days (55,275 missing ticker-day pairs). Root cause: the change table moved to the Wikipedia page "Historical components of the S&P 500", the parser silently fell back to the "Date added" column of the current members (additions only), the GitHub CSV backup ended in 2019, and `IndexSyncer` could only close intervals that were already open and dated them with `date.today()` ([LEARNINGS.md](LEARNINGS.md) 2026-10-16).

**Decision:**
- **Source:** the parser reads "Historical components of the S&P 500" (MultiIndex headers handled) and "…of the Nasdaq-100". The additions-only fallback is flagged `complete=False` (logged at ERROR) and can never seed: `POST /ops/seed/index-membership` refuses incomplete sources with 502 and writes nothing (`seed_blockers`). The GitHub CSV (`fja05680/sp500`, now the "(Updated)" file) is a cross-check only, not a seeding source.
- **Rename convention:** a ticker rename is **one continuous interval under the CURRENT symbol**; old symbols get no interval. Reasons: Alpaca serves the full history under the new symbol, and several old symbols were re-used by unrelated securities (FB = ProShares ETF, LB = LandBridge, PARA = Banzai, INFO = Harbor ETF). 24 documented renames live in `universe/ticker_aliases.py` (21 from the inventory + FBHS→FBIN, PSTG→P, PSKY→SKYD; WRK→SW is treated as a rename, open question: new entity?). Mergers with their own price history (PARA→PSKY, DISCA/DISCK→WBD) are real exits. Special cases: CDAY→DAY is one interval keyed DAY (2021-09-20 … 2026-02-09, early prices fetched under CDAY); FBHS→FBIN is keyed FBIN and ends 2022-12-19 (real exit).
- **Manual event:** Paramount Global (PARA) left and Paramount Skydance joined on 2025-08-08; this is in neither the Wikipedia table nor the GitHub snapshots, so `rebuild_sp500_membership.py` adds it as a manual event (`source='manual'`; the successor is keyed by its current symbol SKYD, formerly PSKY; skipped automatically once Wikipedia lists it). The existing `PSKY` row (from 2021-01-01, 966 phantom member days) is deleted. `universe.PARA` currently holds Banzai International, so Paramount's PARA price series is replaced via `onboard_removed_members.py --fix-para`.
- **Ongoing maintenance:** `IndexSyncer` dates intervals with the effective change date (the sync date only as a logged fallback) and re-keys the open interval on a rename (alias list first, then Alpaca `name_change`). New weekly job `universe_validation` (Sunday 13:00) deactivates tickers that are untradable on Alpaca – unless an index interval is still open (then a warning, run status `partial`) – and writes renames/mergers from Alpaca corporate actions (`universe/corporate_actions.py`) into the run notes ([SCHEDULER.md](SCHEDULER.md)).
- **Price backfill rule:** exits get inactive `universe` rows (`added_by='sp500_history'`) and SIP price rows (`source='alpaca_backfill'`, `adjustment=all`). For delisted or re-used symbols the Alpaca `asof` parameter is the **first member day** of the request, not the last: `asof` = last day returned 2 bars of an ETF that re-used INFO after IHS Markit's delisting.
- **Repair in three steps** (runbook: section 8 of the inventory; execution log 2026-10-17 there): (1) code – committed 2026-10-16 (`c290f65`, `3d85fcf`), deployed 2026-10-17; (2) data (applied 2026-10-17, dry runs matched the runbook; follow-up scope: next entry) – `scripts/repair/rebuild_sp500_membership.py` (expected +105 / ~9 updated / −1 `sp500` rows, members 503–506 on every session since 2021, refuses more than 10 deletes or member counts outside 495–510, CSV backup, `--restore`) and `scripts/repair/onboard_removed_members.py --fix-para` (68 inactive universe rows, ~40,100 price rows, technical indicators); both are dry runs by default and `--apply` needs Sebastian's OK; (3) full feature rebuild and re-run of the analyses (both done on 2026-10-17, see Consequence).

**Rationale:** Index exits are the survivorship signal; a history without them is worse than none because it looks complete. Fixing the source, the sync and the convention first (step 1) means the repaired data stays correct after the next monthly sync; one interval per entity under the current symbol keeps point-in-time joins on `ticker` consistent with the price tables.

**Consequence:**
- **Status 2026-10-17: repair complete.** Until then all analyses that read the S&P 500 point-in-time universe since August 2026 (R1, the K1–K4 research steps incl. the index-addition event study, the monthly feature analysis, walk-forward, Sprint 10 readiness) ran on a survivor-biased universe. Membership and prices were repaired on 2026-10-17 (step 2), the full feature rebuild finished at 17:13 (`feature_snapshots` 676,953 rows vs. 627,412 before, every point-in-time member has a snapshot on every session), and the analyses were re-run read-only on the repaired data: index-addition event study −0.320 % vs. universe (before −0.321 %), walk-forward hit model Ø holdout AUC 0.511 / 0.499 and holdout difference to the universe +0.014 % n.s. (before +0.075 % n.s.), barrier backtest of the context-pack score without a significantly positive net return, Sprint 10 readiness 29/31 GO. **No research conclusion (K1, B, D2) changed** – the null results were not an artefact of survivorship bias ([reports](reports/2026-10-17_hit_model_walkforward.md), [SESSION_LOG.md](SESSION_LOG.md) Session 34). The monthly `feature_analysis` was re-run on the repaired data on 2026-10-17 (with Sebastian's OK): 568,079 snapshots, 752 tickers, still no significant feature.
- The feature rebuild changes 1,242 of 1,265 snapshot dates (+~50,500 ticker-day rows, 12 of 108 feature columns empty for the backfilled delisted tickers, as for ATVI today); `breadth_*` features change for every date.
- The Nasdaq-100 intervals are untouched (the DB already holds 56 exits there; every statement of the rebuild is restricted to `index_name = 'sp500'`). The four exits of Q4 2021 leave the retention window on 2027-01-01.
- Open uncertainties are listed in section 7 of the inventory (entity vs ticker key, WRK→SW, IEX vs SIP quality of the old delisted backfill, non-price source tables for the 70 tickers).

---

### [2026-10-17] Index Members Are Never Blacklisted; Scope of the Repair for Current Members

**Context:** The first dry run of `onboard_removed_members.py` on 2026-10-17 (before the membership rebuild was applied) found nothing to onboard – by design, it reads `index_membership` – but surfaced a false positive: BF.B (Brown-Forman Class B, a current S&P 500 member) was `is_active = false` and in `ticker_blacklist` as `MUTUALFUND` / `manual_enrichment` since 2026-04-15. The sector enrichment asked Yahoo for the raw symbol `BF.B` before `to_yahoo_symbol` mapped it to `BF-B`; the wrong lookup returned a fund, and the learned ETF filter blacklisted and deactivated the ticker. BF.B never had a price row, and 1,266 feature snapshots existed without price data. It was the only false positive among the 54 blacklist entries (the other 53 are ETFs).

**Decision:**
- **Index membership proves an equity.** The learned ETF filter never blacklists or deactivates a ticker with an open `index_membership` interval: `apply_sector_records` (monthly `index_sync` enrichment, UI backfill) and `NewTickerOnboarder` (`_verify_equity_type` and `_enrich_sector`, which now uses the shared `apply_sector_records`) log a warning and take the normal sector path (`8de96a1`, `d03955c`). The benchmark/sector ETFs (`BENCHMARK_TICKERS`) are never deactivated by `apply_sector_records` either.
- **Reactivation by the repair:** `onboard_removed_members.py --add-current-members` reactivates inactive current S&P 500 members and removes their `ticker_blacklist` rows after a CSV backup; applied on 2026-10-17 for BF.B.
- **IEX→SIP overwrite accepted:** the repair re-fetched the tail of ANSS, ATVI and XLNX and overwrote 1,555 rows of the old IEX backfill (ANSS 950, ATVI 512, XLNX 93) with SIP bars (`source = 'alpaca_backfill'`), in line with the SIP-only price policy.
- **Current members onboarded active by the repair:** SKYD, TWLO and VYLR were inserted as active universe rows and priced (TWLO with its full history as indicator warm-up) instead of waiting for the monthly `index_sync` of 2026-11-01.
- **Banzai International is not added:** the company behind the PARA symbol collision is no index member and not part of the project; its 301 price rows under PARA were deleted and replaced by Paramount's series.

**Rationale:** A false positive in the blacklist silently removes a current member from prices, features and every point-in-time universe, and nothing flags it. Membership of the S&P 500 / Nasdaq 100 is stronger evidence than a Yahoo `quoteType`, which can come from the wrong symbol form. Waiting for the monthly sync would have left three current members without prices (and without features) for two more weeks; the old IEX rows were of lower quality than SIP and the tail sessions had to be fetched anyway.

**Consequence:**
- Active universe 760 (757 − PARA + SKYD/TWLO/VYLR + BF.B). The check `select b.ticker from signals.ticker_blacklist b join signals.index_membership m on m.ticker = b.ticker where m.valid_to is null` must return no rows (standing check after every `index_sync`).
- The ANSS/ATVI/XLNX rows can no longer be distinguished from the other backfilled rows by `source`; the old IEX values are not backed up.
- Numbers and backups of the run: runbook section 8, "Execution log 2026-10-17" ([report](reports/2026-10-16_sp500_survivorship_inventory.md)).
