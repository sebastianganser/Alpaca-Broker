# DECISIONS_DATA.md – Data Source & Collector Decisions

> Decisions related to data sources, collectors, data quality, and universe management.
>
> See also: [INDEX.md](INDEX.md) · Related: [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) · [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md)

**Last updated:** May 2026

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
