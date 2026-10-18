# DATA_SOURCES.md – Data Source Catalog

> Detailed documentation of each data source: how to access it, what it delivers, and its limitations.
> Extended whenever new sources are added.
>
> See also: [INDEX.md](INDEX.md)

**Last updated:** 18 October 2026

---

## Overview

| Source | Category | Cost | Frequency | Delay | Sprint | Status |
|---|---|---|---|---|---|---|
| **Alpaca Market Data** | Prices (OHLCV) | Free (SIP, IEX fallback) | Daily | ~minutes after close | 1b | ✅ Primary |
| **Alpaca News API** | News Headlines | Free | Daily | Near real-time | 8c | ✅ Active |
| **yfinance** | Prices (fallback), Fundamentals | Free | Daily | ~20 min EOD | 1 | ⏸ Fallback |
| **arkfunds.io** | Smart Money (ARK ETFs) | Free | Daily EOD | ~1 hour | 2 | ✅ Active |
| **SEC EDGAR – Form 4** | Insider Trades | Free | Rolling | 2 business days (legal) | 3 | ✅ Active |
| **SEC EDGAR – Form 13F** | Institutional Holdings | Free | Quarterly | Up to 45 days | 3 | ✅ Active |
| **Senate eFD** | Politician Trades | Free | Weekly | 30–45 days | 4 | ✅ Active |
| **yfinance – Fundamentals** | P/E, Revenue, EPS | Free | Weekly | ~ | 5 | ✅ Active |
| **yfinance – Ratings** | Analyst Upgrades/Downgrades | Free | Daily | ~ | 5 | ✅ Active |
| **yfinance – Earnings** | Earnings Dates + Surprises | Free | Weekly | ~ | 5 | ✅ Active |
| **ProsusAI/finbert** | NLP Sentiment Scoring | Free (local) | Daily | <1 min | 8c | ✅ Active |
| **FRED (St. Louis Fed)** | Macro Indicators | Free | Daily | ~6h after US close | 9.5b | ✅ Active |
| **yfinance – Options** | Implied Volatility | Free | Daily | ~20 min EOD | 9.5b | ✅ Active |
| **Massive (ex Polygon)** | Short Volume (daily) + FINRA Short Interest (twice a month) | Free | Daily (volume) / weekly run (interest) | T+1 (volume) / ~7 business days after settlement date (interest) | 9.5c | ✅ Active |
| **Wikipedia – index change tables** | Index membership history (S&P 500, Nasdaq 100) | Free | Monthly (`index_sync`) | Effective date of the change (editor lag not measured) | 9.5a | ✅ Active (source page changed, parser fixed 2026-10-16) |
| **Alpaca Corporate Actions** | Renames, mergers (explains disappeared tickers) | Free | Weekly (`universe_validation`) + monthly (`index_sync`) | Process date of the action | 9.5c | 🟡 Code committed 2026-10-16, deploy pending |
| **SEC EDGAR – Filings** (submissions API) | 8-K (+ item codes, press release / report excerpts), 10-Q/10-K, 144, 13D/13G, S-1/S-3, DEF 14A, NT 10-K/Q, 6-K | Free | Daily 04:00 CET | Minutes after acceptance (collected the same night, 22:00 ET cut-off) | 18.10.2026 | 🟡 Code ready 2026-10-18, deploy + backfill pending |
| **SEC EDGAR – XBRL financials** (companyfacts API) | Quarterly revenue, profit, EPS, cash flow, FCF, cash, debt, shares | Free | Weekly (Sunday 14:00) | With the 10-Q/10-K filing | 18.10.2026 | 🟡 Code ready 2026-10-18, deploy + backfill pending |

---

## 1. Alpaca Market Data API (Primary Price Source)

**Category:** Market data (OHLCV)
**API Endpoint:** `https://data.alpaca.markets/v2/stocks/bars`
**Docs:** https://docs.alpaca.markets/reference/stockbars
**Status:** ✅ Implemented (Sprint 1b)

### What It Delivers
Multi-symbol batch endpoint with daily OHLCV data:
- `o` = Open, `h` = High, `l` = Low, `c` = Close (adjusted with `adjustment=all`)
- `v` = Volume, `vw` = VWAP, `n` = Trade count
- **adj_close = close** (Alpaca with `adjustment=all` already provides split- and dividend-adjusted values)

### Configuration
- **Feed:** `sip` (`ALPACA_DATA_FEED`, default; free tier allows SIP when the request ends ≥ 15 min ago), automatic IEX fallback marks the run `partial`
- **Batch size:** 100 tickers per request
- **644 tickers in 7 requests (<10s total runtime)**
- **Rate limit:** 200 req/min (free tier) → no problem

### Best Practices
- Prefer multi-symbol endpoint over individual queries
- `adjustment=all` for split+dividend-adjusted values
- Pagination via `next_page_token` for large time ranges
- Retry with exponential backoff on 429/5xx

---

## 2. yfinance (Fallback for Prices + Fundamentals)

**Category:** Market data (fallback), fundamentals, ratings, earnings
**Python package:** `yfinance`
**Status:** ✅ Active for fundamentals/ratings/earnings (Sprint 5), ⏸ Fallback for prices

> **Since Sprint 1b:** yfinance replaced by Alpaca as primary price source.
> **Sprint 5:** yfinance actively used for fundamentals, analyst ratings, and earnings calendar.
> **Post-Sprint 7:** yfinance additionally used for sector/industry enrichment.

### What It Delivers

**Fundamentals (via `ticker.info`):**
- `marketCap`, `trailingPE`, `forwardPE`, `priceToSalesTrailing12Months`, `priceToBook`
- `enterpriseToEbitda`, `profitMargins`, `operatingMargins`, `returnOnEquity`
- `totalRevenue`, `revenueGrowth`, `trailingEps`, `debtToEquity`, `currentRatio`
- `dividendYield`, `beta`
- Since 2026-10-18 (migration 040, same `info` call - no extra requests): `sharesOutstanding`, `floatShares`, `heldPercentInstitutions`, `heldPercentInsiders`, `shortPercentOfFloat`, `freeCashflow`, `totalCash`, `totalDebt`, `grossMargins`, `recommendationMean`, `recommendationKey`, `numberOfAnalystOpinions` (ratios are decimal fractions; `recommendationKey` `'none'` is stored as NULL; amounts are in the listing currency; the plausibility guards in `yfinance_client._PLAUSIBILITY_RULES` null format breaks such as percent instead of fraction)

**Analyst Ratings (via `ticker.upgrades_downgrades`):**
- `Firm`, `ToGrade`, `FromGrade`, `Action` (up/down/main/init)
- Lookback: 30 days

**Earnings Calendar (via `ticker.get_earnings_dates()`):**
- `EPS Estimate`, `Reported EPS`, `Surprise(%)`
- Limit: last 4 earnings per ticker

**Sector/Industry (via `ticker.info`):**
- `sector` (e.g., "Technology", "Healthcare")
- `industry` (e.g., "Semiconductors")

### Configuration (Sprint 5)
- **Rate limiting:** 0.5s between tickers, 3s between batches (of 50)
- **644 tickers in ~25 minutes** per collector run
- **Schedule:** Night slot 01:00–03:00 CET

### Limitations
- **Unofficial API** – Yahoo can make changes any time
- **Rate limiting** – aggressive with many requests
- **No SLA** – use at own risk
- **No batch endpoint** for `info` – each ticker is a separate call

---

## 3. ARK Funds – Holdings via arkfunds.io API

**Category:** Smart money (actively managed ETFs)
**API Endpoint:** `https://arkfunds.io/api/v2/etf/holdings?symbol={ETF}`
**Status:** ✅ Implemented (Sprint 2)

> **Note:** The direct CSV URL from ark-funds.com returns 403 (Cloudflare protection).

### What It Delivers
JSON response per ETF with holdings array: `fund`, `date`, `ticker`, `company`, `cusip`, `shares`, `market_value`, `share_price`, `weight`, `weight_rank`.

### Tracked ETFs
ARKK, ARKQ, ARKW, ARKG, ARKF, ARKX (high priority), PRNT, IZRL (low priority).

### Known Pitfalls
- Cash positions (e.g., "GOLDMAN FS TRSY OBLIG") filtered via regex
- International tickers (e.g., KMTUY, BYDDY) only included if tradable on Alpaca
- Third-party risk: arkfunds.io is not an official ARK source

---

## 4. SEC EDGAR – Form 4 (Insider Trades)

**Category:** Insider transactions (mandatory filing)
**API:** `https://data.sec.gov/submissions/CIK{CIK}.json`
**Status:** ✅ Implemented (Sprint 3)

### What It Delivers
Every purchase/sale by an "insider" (CEO, CFO, directors, >10% shareholders).

### Signal Value
- **Insider buys:** Strong signal (CEOs buy with own money only with conviction)
- **Insider sells:** Weak signal (planned programs, taxes, diversification)
- **Cluster buys:** Multiple insiders buy in short timeframe → very strong signal

### Implementation Details
- **Collector:** `Form4Collector` (universe-driven, 644 tickers)
- **Client:** `SECClient` with rate limiting (10 req/s) and CIK mapping
- **Derived:** `InsiderClusterComputer` detects cluster buys (≥2 insiders in 21 days)
- **Important:** Use company CIK (not filer CIK) for archive URLs

### Backfill & Data Quality
- **Historical backfill:** `scripts/backfill_form4.py` – resume-safe, ~3 years of data
- **Result:** 313,544 trades across 647 tickers (96% universe coverage)
- **Outlier cleanup:** `scripts/cleanup_insider_outliers.py` – removes trades outside `DATA_START_DATE..today`
- **Verification:** `scripts/verify_insider_backfill.py` – distribution analysis + depth check
- **Known issues:** SEC XML `transaction_date` can contain typos (year 0024) or future vesting dates (2033). Always validate against `DATA_START_DATE`.

---

## 5. SEC EDGAR – Form 13F (Institutional Holdings)

**Category:** Quarterly reports from large funds (>$100M AUM)
**Status:** ✅ Implemented (Sprint 3)

### What It Delivers
Complete holding lists, quarterly, with up to 45-day delay.

### Usage
Not as a signal for individual trades, but as a **feature**: "How many top-13F holders hold this ticker?"

### Top Filers
Berkshire Hathaway, Scion Capital, Pershing Square, Tiger Global, Renaissance Technologies, Bridgewater Associates, Citadel, Two Sigma, D.E. Shaw, Millennium, Point72, Greenlight, Baupost, Third Point, Icahn, Elliott, Duquesne, Coatue, Appaloosa, ARK.

### Implementation Details
- **Infotable detection:** 4-stage strategy (SEC allows arbitrary filenames)
- **Schedule:** Weekly Sunday 10:00 CET

---

## 6. Senate eFD – Politician Trades (Congress)

**URL:** https://efdsearch.senate.gov/search/
**Status:** ✅ Implemented (Sprint 4)

### What It Delivers
All Periodic Transaction Reports (PTRs) from US senators – official financial disclosure per STOCK Act. Politician name, ticker, transaction type, date, amount range, owner.

### Access
- **Free** – official US government source
- **No API token** – DataTables AJAX endpoint (JSON)
- **TLS fingerprinting:** Senate eFD blocks Python `requests` (JA3 hash detection)
- **Solution:** `curl_cffi` with Chrome impersonation

### Limitations
- **Delay: 30–45 days** (STOCK Act allows up to 45 days)
- **Amount ranges** instead of exact amounts
- **Senate only** – House PTRs are PDF-only (future enhancement)

### Realistic Assessment
Due to delay, probably not a strong alpha signal, but useful as a feature in aggregate.

---

## 7. Alpaca News API (Sprint 8c)

**Category:** Financial news headlines
**API Endpoint:** `https://data.alpaca.markets/v1beta1/news`
**Docs:** https://docs.alpaca.markets/reference/news-1
**Status:** ✅ Implemented (Sprint 8c)

### What It Delivers
Financial news articles with headline, summary, source, author, published_at, and associated stock symbols.

### Configuration
- **Lookback:** 3 days per daily run
- **Page size:** 50 articles/request, paginated
- **Deduplication:** `article_id` unique constraint
- **Multi-ticker:** One article can reference 0..N tickers via `symbols` array
- **Schedule:** Daily 00:00 CET

### Best Practices
- Use `symbols` array with PostgreSQL GIN index for efficient ticker lookups
- Mark articles without specific tickers as `is_global = True`
- Only store articles with English headlines

---

## 8. ProsusAI/finbert – Sentiment Scoring (Sprint 8c)

**Category:** NLP sentiment analysis
**Model:** `ProsusAI/finbert` (110M parameters, BERT-based)
**Status:** ✅ Implemented (Sprint 8c)

### What It Delivers
Sentiment scores for financial news headlines:
- **sentiment_label:** `positive`, `negative`, `neutral`
- **sentiment_score:** -1.0 to +1.0 (continuous)
- **confidence:** Model confidence (softmax probability)

### Configuration
- **Local inference:** CPU-based, no API calls, no costs
- **Batch processing:** `batch_size=32`, ~23s for 1700 scores
- **Max token length:** 512 tokens (headlines auto-truncated)
- **Model pre-cached** in Docker image (~440 MB) to avoid runtime downloads
- **Schedule:** Daily 00:30 CET (after news collector)

### Signal Value
- **Headline-only scoring:** Intentional – avoids paywall issues, headlines carry most information
- **Per-ticker + global:** Each article generates one score per mentioned ticker + one global (NULL ticker)
- **Rolling aggregates:** 7d and 30d windows, momentum (7d–30d spread), negative article count

### Limitations
- **Headline bias:** FinBERT scores reflect headline sentiment, which may be sensationalized
- **English only:** Non-English headlines may produce unreliable scores
- **Model age:** FinBERT trained on pre-2020 financial text; may miss recent terminology
- **Planned upgrade:** Claude Haiku for more nuanced financial sentiment (Sprint 9+)

---

## 9. yfinance – Analyst Estimates & Revisions (Sprint 9.5a)

**Purpose:** Daily capture of analyst consensus estimates (EPS & Revenue), revision counts, and the rolling 90-day EPS trend window from Yahoo Finance.

**API Properties Used:**
- `ticker.earnings_estimate` → EPS avg/low/high, analyst count, year-ago, growth
- `ticker.revenue_estimate` → Revenue avg/low/high, analyst count, year-ago, growth
- `ticker.eps_trend` → EPS consensus at current, 7d, 30d, 60d, 90d ago
- `ticker.eps_revisions` → Count of up/down revisions in 7d and 30d

**Periods Collected:** `0q` (current quarter), `+1q` (next quarter), `0y` (current year), `+1y` (next year)

**Key Feature Signals:**
- **Revisions Momentum** (eps_current vs eps_30d_ago) – one of the strongest short-term predictive factors in quantitative finance
- **Upward vs Downward Bias** (rev_up_30d vs rev_down_30d) – net revision direction
- **Consensus Spread** (eps_high - eps_low) – analyst disagreement as uncertainty proxy

**Schedule:** Daily 01:30 CET (after analyst_ratings, before feature_pipeline)

**Critical Note:** Yahoo provides a **rolling 90-day window** for EPS trend data. This collector must run daily without fail — every missed day permanently loses one day of irrecoverable revision history. The `raw JSONB` column stores the complete API response for future-proofing.

**Rate Limiting:** Shared `YFinanceClient` with 0.5s inter-ticker delay, 3s batch pauses.

---

## 10. Massive API – Short Interest / Volume (Sprint 9.5c)

**Category:** Short selling data (FINRA reported)
**API Endpoints:** `https://api.massive.com/stocks/v1/short-volume` (daily short volume), `https://api.massive.com/stocks/v1/short-interest` (FINRA settlement-date positions)
**Docs:** https://massive.com/docs (formerly https://polygon.io/docs)
**Status:** ✅ Implemented (Sprint 9.5c)

> **Note:** Polygon.io rebranded to Massive in October 2025. The old `api.polygon.io` base URL still works in parallel, but new implementations should use `api.massive.com`.

### What It Delivers
Daily short volume data per ticker from FINRA:
- `short_volume` – shares sold short that day
- `total_volume` – total consolidated volume
- `short_volume_ratio` – short_volume / total_volume

In addition the twice-monthly FINRA **short interest** (outstanding positions per settlement date) – see "FINRA Short Interest" below.

### Configuration
- **API Key:** Free tier at https://massive.com (registration required)
- **Rate Limit:** 5 requests/minute (free tier) → 12s between tickers
- **~765 tickers in ~2.5 hours** per daily run
- **Schedule:** Daily 00:15 Europe/Berlin (short volume, `short_interest_collector`); Sunday 12:00 (FINRA short interest, `short_interest_positions_collector`)
- **Auth:** API key as `Authorization: Bearer` header (never as a query parameter – URLs end up in logs)

### Feature Signals
- `short_volume_ratio_5d` – 5-day average short volume ratio
- `short_volume_ratio_20d` – 20-day average short volume ratio
- `short_volume_change_20d` – 20-day momentum change in short ratio

### FINRA Short Interest (settlement data, since 2026-10-16)
Outstanding short positions per settlement date, published twice a month. A **different dataset** than the daily short volume above; stored in `signals.short_interest` ([DATABASE.md](DATABASE.md)).
- **Collector:** `collectors/short_interest_positions_collector.py` (`ShortInterestPositionsCollector`, `collection_log` name `short_interest_positions`), job `short_interest_positions_collector`, **Sunday 12:00 Europe/Berlin**, not part of `NIGHTLY_UPSTREAM` ([SCHEDULER.md](SCHEDULER.md)).
- **Requests:** one reference request (AAPL) lists the settlement dates; then **one bulk request per settlement date** (all tickers in one page), filtered client-side to all universe tickers incl. inactive ones. Fetched: dates not stored yet + the 2 most recent stored dates (FINRA revisions) + stored dates with < 50 % of the median row count; never before `data_start_date()`. 12 s pause between requests, retries on 429/5xx, `next_url` to foreign hosts is refused. Upsert on `(ticker, settlement_date, source)`. A normal run needs ~3 requests.
- **Initial history:** `scripts/repair/collect_short_interest.py` (~120 requests, ~25 min at 12 s; `--refetch-all` fetches every date again, e.g. for tickers that joined later). Feature history: `scripts/repair/backfill_short_interest_features.py` (dry run by default, `--apply`).
- **Features** (migration 036, `FEATURE_VERSION` 2026.10-3, group "Short Interest"): `short_interest_days_to_cover`, `short_interest_change_1m` (rules: `derived/short_interest_features.py`). **Availability rule (no lookahead):** a settlement date is usable for day `d` only if it is <= the 9th NYSE session before `d` (`SI_PUBLICATION_LAG_SESSIONS = 9`: FINRA publishes ~7 business days after settlement + 2 sessions margin); newest usable settlement older than 40 days → NULL; `change_1m` = SI(newest) / SI(2 usable settlements earlier) − 1 within 45 days, earlier SI must be > 0. `days_to_cover = 999.99` / `avg_daily_volume = 0` are stored as NULL.
- **Use:** to be evaluated in the monthly feature analysis before any score use – expected value mainly as risk filter / longer horizons, not as a +1 %/14-day edge ([DECISIONS_DATA.md](DECISIONS_DATA.md) 2026-10-16).
- **Endpoint facts** (history since 2017-12-29, rows per date, fields, sentinel): see the "Short interest endpoint verified" bullet under Limitations.

### Limitations
- **Free tier rate limit** makes full universe scan slow (~2.5h)
- **T+1 delay** – data available next trading day
- **Short volume ≠ short interest** – volume is daily flow, interest is outstanding positions (bi-monthly FINRA data, separate endpoint)
- **Short interest endpoint verified (2026-10-16, probe; collected since the same day, see above):** `GET /stocks/v1/short-interest` works on the free tier. History reaches back to 2017-12-29 (AAPL: 211 settlement dates, twice a month). One request per `settlement_date` returns **all** tickers in one page (2026-09-30: 22,579 rows incl. OTC, no `next_url`) → backfill from `data_start_date()` ≈ 120 requests (~25 min at 12 s), ongoing 2 requests per month. Fields: `ticker`, `settlement_date`, `short_interest`, `avg_daily_volume`, `days_to_cover` (`999.99` when `avg_daily_volume` is 0 → treat as NULL). No publication date in the payload – FINRA publishes several business days after the settlement date (2026-09-30 was available on 2026-10-16), so features need a conservative availability lag (lookahead) – implemented as 9 NYSE sessions.

---

## 11. Wikipedia – Index Membership History (Sprint 9.5a, source fixed 2026-10-16)

**Category:** Reference data (index constituents and their changes)
**Pages:** S&P 500 changes: https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500 (table with `Added` / `Removed` column groups, effective dates, ~410 rows since 1957) · Nasdaq-100 changes: https://en.wikipedia.org/wiki/Historical_components_of_the_Nasdaq-100 · current constituents: https://en.wikipedia.org/wiki/List_of_S%26P_500_companies and https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies
**Status:** ✅ Implemented (`universe/wikipedia_index_history.py`, `universe/index_sync.py`); S&P 500 source corrected 2026-10-16

> **Source change (verified 2026-10-16):** the S&P 500 change table is **no longer** on "List of S&P 500 companies" (that page now holds only the current constituents and a navbox). The parser used to read it there, found nothing and silently fell back to the "Date added" column of the current members – additions only, no exits ([LEARNINGS.md](LEARNINGS.md) 2026-10-16, [DECISIONS_DATA.md](DECISIONS_DATA.md) 2026-10-16). It now reads "Historical components of the S&P 500" (MultiIndex headers) and reports `complete=False` if a source delivers no removals; such a result never seeds `index_membership` (`POST /ops/seed/index-membership` answers 502).

### What It Delivers
Per change: effective date, added ticker/security, removed ticker/security, reason text. A ticker rename appears as "NEW added / OLD removed" and is **not** an exit – events are normalized to the current symbol (`universe/ticker_aliases.py`, [DATABASE.md](DATABASE.md) `index_membership`).

### Cross-check: GitHub `fja05680/sp500`
The maintained file is `S&P 500 Historical Components & Changes (Updated).csv` (last row 2026-08-18 on 2026-10-16). Snapshot dates differ by 1–2 days from the effective dates, renames show up as exits, and it lags the index by weeks → **cross-check only, never a seeding source**. The original file (`…Changes.csv`) ends on 2019-01-11 and yields no change since 2021.

### Limitations
- Third-party, community-edited page: a layout change can break the parser (it then returns an incomplete result and logs an ERROR instead of falling back silently).
- Two events are in neither the table nor the GitHub snapshots: the Paramount Global exit / Paramount Skydance entry of 2025-08-08 (added as a manual event by the repair script) and the rename PSKY→SKYD of 2026-10-06 (known from Alpaca and `ticker_aliases.py`).
- The most recent changes (after the last GitHub snapshot) rest on Wikipedia alone.

---

## 12. Alpaca Corporate Actions (since 2026-10-16, code only)

**Category:** Corporate events (renames, mergers) – read-only, explains why a ticker disappeared
**API Endpoint:** `GET https://data.alpaca.markets/v1/corporate-actions` (same key headers as the other Alpaca clients; `universe/corporate_actions.py`)
**Status:** 🟡 Code committed 2026-10-16, deploy pending

- **Requested types:** `name_change` (old → new symbol) and `cash_merger` / `stock_merger` / `stock_and_cash_merger` (acquiree → acquirer or cash). Symbols are sent in batches of 25 with pagination.
- **Used by:** the weekly `universe_validation` job (renames/mergers of the last 365 days for deactivated tickers go into the run notes; a rename whose new symbol is missing from `universe` is a warning) and by `IndexSyncer` (rename detection: alias list first, then Alpaca `name_change` of the last 400 days → the open `index_membership` interval is re-keyed instead of closed).
- **Caveats:** Alpaca serves history of renamed or delisted symbols inconsistently – e.g. `CDAY` bars only under the old symbol, a re-used symbol (`INFO`, `PARA`) returns the new holder's bars unless the `asof` date of a bars request is set to a day when the original company traded (use the first member day); `GET /v2/assets` answers 404 for some delisted symbols although bars exist (details: [inventory](reports/2026-10-16_sp500_survivorship_inventory.md), section 4).

---

## 13. SEC EDGAR – Filings and Press Releases (since 2026-10-18, code ready, deploy pending)

**Category:** Company filings (metadata + text excerpts), point-in-time
**API:** `https://data.sec.gov/submissions/CIK##########.json` (filing list per company), `https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/<accession>.txt` (full-submission text, streamed for the press release)
**Auth / fair access:** none; `User-Agent` with contact (`SEC_USER_AGENT`), <= 10 requests/s (`SECClient`: 9/s)
**Collector:** `SecFilingsCollector` (`collectors/sec_filings_collector.py`), job `sec_filings_collector`, daily 04:00 Berlin, collector name `sec_filings`
**Tables:** `sec_filings`, `sec_filing_texts` ([DATABASE.md](DATABASE.md))

### What It Delivers

One row per filing of every active universe company that has a CIK (share classes such as GOOG/GOOGL share one CIK and are fetched once; `tickers` lists all of them):

| Form | Why it is collected |
|---|---|
| `8-K`, `8-K/A` | Current reports with **item codes** (`2.02` results, `7.01`/`8.01` Reg FD / other events, `5.02` officer changes, `1.01`/`2.01` agreements/acquisitions, `4.02` non-reliance on financial statements, `1.05` cyber incident, `3.01` delisting notice, ...) |
| `10-Q`, `10-K` (+ `/A`), `6-K`, `20-F`, `40-F` | Periodic reports; the foreign-private-issuer forms of the few ADRs in the universe |
| `NT 10-K`, `NT 10-Q` | Late-filing notices - a red flag |
| `144` | Notice of a planned insider sale |
| `SC 13D/G` (+ `/A`) and, since the structured-data switch of Dec 2024, `SCHEDULE 13D/G` (+ `/A`) | Stakes >= 5 %: activists, passive holders |
| `S-1`, `S-3`, `S-3ASR` (+ `/A`) | Registration of securities: dilution / shelf offerings |
| `DEF 14A`, `DEFM14A` | Proxy statements: pay, votes, mergers |

**Not collected:** Forms 3/4/5 (`form4_collector`), 13F (`form13f_collector`), `424B*` prospectus supplements (thousands of structured-note supplements per day from the banks), `425` / `SC TO` merger communications, exhibits of 10-K/10-Q.

**Texts (`sec_filing_texts`):** an 8-K with item `2.02`, `7.01` or `8.01` gets the press release (`EX-99.1`, then `EX-99`, then the lowest other `EX-99.x`; if the report has none, the report body) and an 8-K with one of the event items `1.01 1.02 1.03 1.05 2.01 2.05 2.06 3.01 4.01 4.02 5.02` gets the report body without its cover page - each as a cleaned plain-text excerpt of the first ≈ 3,000 characters (`full_length` = length before the cut). HTML / inline-XBRL markup, the hidden XBRL header, scripts and styles are removed. One streamed request per filing (the download stops after the wanted document). A failed text request leaves `texts_fetched_at` NULL and is retried by the next runs for as long as the filing is inside the 14-day lookback.

### Point-in-time

`acceptance_datetime` is the instant EDGAR accepted the filing (UTC); a filing is public from then on, `NULL` means "from the end of `filing_date`". **Finding of 2026-10-18:** the `acceptanceDateTime` strings of the submissions JSON are labelled `Z` but are the Eastern wall clock **plus 8 hours (summer) / 10 hours (winter)** - checked on ten filings of six filers (2016-2026) against the "Accepted" time of the filing index page and the `<ACCEPTANCE-DATETIME>` of the full-submission text (Apple 8-K: JSON `2026-07-31T00:30:28.000Z`, index page `2026-07-30 16:30:28`). The collector stores the corrected instant and the raw string (`acceptance_raw`). `SECClient.parse_acceptance_datetime` (Form 4 / 13F) reads the same string as Eastern wall clock, i.e. 8-10 hours too late - conservative (never early) but it delays most intraday insider filings by a session in "public before 16:00 ET" rules; not changed here.

### Schedule and Request Volume

- **Daily 04:00 Berlin = 22:00 ET**, EDGAR's cut-off for same-day 8-Ks: the run sees the complete US day including after-the-close earnings releases (16:00-17:30 ET), lies outside the 22:30-03:00 collector load and 30 minutes before the nightly chain. 14-day lookback (robust against missed days).
- Per run: 1 (`company_tickers.json`) + ≈ 650 submissions requests (one per CIK, 0.1-1 MB) + one request per filing text (≈ 10-60 on a normal day, up to ≈ 150 in peak earnings weeks) at 9 requests/s ≈ 5-10 minutes. `filings.recent` of the submissions JSON holds the newest ≈ 1,000 filings (at least one year); older pages are read when the lookback needs them.
- **Initial backfill:** `scripts/repair/backfill_sec_filings.py` (dry run by default, `--apply`, `--days 120`, `--tickers`): 120 days ≈ 650 + 2,000-4,000 text requests ≈ 15-40 minutes. **Needs Sebastian's OK on production.**
- Not yet an input of the nightly chain or the context pack: wiring is a separate step (add `sec_filings: False` to `NIGHTLY_UPSTREAM` then).

### Limitations

- A company that re-incorporated under a new CIK (holding-company reorganisation, e.g. **ExxonMobil Holdings** 2026, new CIK 2115436 for ticker XOM) only shows the filings of its new registrant in the lookback; older filings sit under the old CIK.
- Item codes only exist for 8-Ks; 6-K filings have none and get no text.
- The excerpt is the start of the document: a press release's headline paragraph, not its tables.
- Tickers without a CIK in `company_tickers.json` (ETFs, delisted/renamed symbols) are skipped.

---

## 14. SEC EDGAR – XBRL Quarterly Financials (since 2026-10-18, code ready, deploy pending)

**Category:** Standardised fundamentals from the company's own filings, point-in-time
**API:** `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` (all us-gaap/dei facts of a company since its first XBRL filing, 0.3-0.5 MB gzip)
**Auth / fair access:** as §13
**Collector:** `SecFinancialsCollector` (`collectors/sec_financials_collector.py`, parsing in `collectors/sec_xbrl.py`), job `sec_financials_collector`, weekly Sunday 14:00 Berlin, collector name `sec_financials`
**Table:** `financials_quarterly` ([DATABASE.md](DATABASE.md))

### What It Delivers

One row per ticker and fiscal quarter inside the retention window (`data_start_date()`): revenue, gross profit, operating income, net income, diluted EPS, operating cash flow, capex, free cash flow (= operating cash flow − capex), cash and equivalents, total debt, shares outstanding, plus `raw_tags` (the us-gaap tag of every figure and the figures that were derived). Yahoo's `fundamentals_snapshot` only has TTM ratios; this table has the quarterly series (growth, margin trend, cash conversion) with the date each quarter became public.

### How the Quarters Are Built (`collectors/sec_xbrl.py`)

- **Facts used:** us-gaap/dei facts in USD (EPS in USD/shares) of 10-Q, 10-K and their amendments; IFRS filers (20-F/40-F) yield no rows.
- **As first reported:** if several filings report the same period (the original 10-Q, next year's comparative column, a restating 10-K/A) the earliest-filed fact wins; `filed` is the date the row's income-statement and cash-flow figures became public (point-in-time key). Restatements are ignored.
- **Quarters:** a direct 3-month fact is used as is. Cumulative facts are differenced: Q2 = 6M − 3M, Q3 = 9M − 6M, **Q4 = fiscal year − 9M** (a difference must span 80-105 days). Cash-flow items are year-to-date only in the 10-Q, so their quarters are always differences. EPS is differenced the same way (approximation, flagged in `raw_tags.derived`); weighted-average shares are never differenced.
- **Tags:** candidate tags per figure in a fixed priority order (e.g. revenue: `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, ...). The base tag is the one with the most recent quarter among the well-covered ones; other tags fill missing quarters only if they agree with the base within 1 % on all common quarters (so a taxonomy hand-over such as Alphabet's is stitched, while XOM's `Revenues` and `RevenueFromContract...` or JPM's `Revenues` and `RevenuesNetOfInterestExpense` - different measures - are never mixed).
- **Balance sheet:** cash, debt and shares are instant facts at the quarter end; `total_debt` = non-current + current long-term debt (+ commercial paper / short-term borrowings when the current portion is tagged separately), otherwise the combined tags; `shares_outstanding` falls back to the cover-page share count of the same filing. Figures first reported in a later filing than the quarter's main filing are NULL (point-in-time).
- **Gross profit** falls back to revenue − cost of revenue (flagged), operating income / capex are NULL for banks.
- Verified 2026-10-18 against the published numbers (Apple Q3 FY2026: revenue 109.417 bn, diluted EPS 2.02; Q4 FY2025 derived 102.466 bn) and on MSFT, WMT, JPM, GOOGL, BRK.B, TSLA.

### Schedule and Request Volume

- **Weekly, Sunday 14:00 Berlin** (after the other Sunday collectors, daytime, away from the nightly load and the seal runs). The full fact history is re-read every week; a new 10-Q/10-K adds a quarter, nothing else changes - the weekly job is its own backfill.
- Per run: 1 + ≈ 650 `companyfacts` requests (one per CIK, share classes once) ≈ 250 MB ≈ 5-10 minutes.
- **Initial run:** `scripts/repair/backfill_sec_financials.py` (dry run by default, `--apply`, `--tickers`) or simply the first scheduled run. **Needs Sebastian's OK on production.**
- Not yet read by the context pack or the features: separate wiring step.

### Limitations

- Companies that re-incorporated under a new CIK only have the quarters of the new registrant (XOM: 2 quarters).
- Banks and insurers have no gross profit / operating income / capex; Berkshire's class-dependent EPS and share counts are not in `companyfacts` (dimensional facts).
- Values are as first reported - a later restatement does not update the row.

---

## 15. Not Yet Implemented – Ideas for Later

- **OpenInsider** – Aggregated Form 4 data with pre-filtered cluster buys
- **Finviz** – Insider screener, news aggregation (scraping gray area)
- **Unusual Whales** – Options flow + politicians (~\$40/month)
- **Reddit/social sentiment** – StockTwits, WSB mentions as contrarian signal
- **On-Chain Data** – Arkham Intelligence, Nansen, Whale Alert

---

## Universal Data Boundary

All data in the system is bounded by `DATA_START_DATE = 2021-01-01` (defined in `trading_signals/config.py`).

- Data before this date is considered irrelevant or an outlier
- All backfill scripts, cleanup scripts, and validation scripts reference this constant
- The retention window will become dynamic once ML determines the optimal lookback period
- Some tables (prices_daily, earnings_calendar) contain data slightly before this date; this will be trimmed in a future cleanup pass

---

## Operational Scripts

| Script | Purpose | When to Run |
|---|---|---|
| `scripts/backfill_form4.py` | Historical insider trade backfill (~3 years) | One-time or after data loss |
| `scripts/backfill_insider_clusters.py` | Recompute insider clusters from trades | After insider backfill |
| `scripts/cleanup_insider_outliers.py` | Remove trades outside `DATA_START_DATE..today` | After backfill |
| `scripts/verify_insider_backfill.py` | Distribution + depth analysis of insider data | After backfill for QA |
| `scripts/sprint8_readiness.py` | Data depth + coverage validation (all tables) | Before starting Sprint 8 |
| `scripts/sprint9_readiness.py` | Feature snapshot + target fill readiness check | Periodic during wait phase |
| `scripts/diag_insider.py` | Quick insider trade diagnostics | Ad-hoc debugging |
| `scripts/backfill_party_mapping.py` | Backfill party affiliation for politician_trades | One-time after C3 deploy |
| `scripts/repair/backfill_sec_filings.py` | Dry run (default) / `--apply` of the SEC filings + press release backfill (120 days) | Once after the deploy of migration 040 (needs Sebastian's OK) |
| `scripts/repair/backfill_sec_financials.py` | Dry run (default) / `--apply` of the SEC XBRL quarterly financials | Once after the deploy of migration 040 (needs Sebastian's OK) |

---

## Data Quality Checks

Every collector should automatically verify:
1. **Completeness:** Were all expected tickers returned?
2. **Plausibility:** Are prices in a reasonable range?
3. **Timeliness:** Is the snapshot date current?
4. **Consistency:** Does an ARK ETF sum to ~100% weight?
5. **Duplicates:** Are we sure we're not storing duplicates?

Errors are logged in `collection_log` and reported via Telegram notification for critical failures.

---

## Terms of Service Note

For all scraping sources, verify before implementation: Is scraping allowed in ToS? Are there robots.txt restrictions? Is the use case (private research project) covered?

Official APIs and government portals (SEC EDGAR, Senate eFD, yfinance) are unproblematic as long as documented rate limits are respected.
