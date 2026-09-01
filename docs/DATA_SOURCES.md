# DATA_SOURCES.md – Data Source Catalog

> Detailed documentation of each data source: how to access it, what it delivers, and its limitations.
> Extended whenever new sources are added.
>
> See also: [INDEX.md](INDEX.md)

**Last updated:** September 2026

---

## Overview

| Source | Category | Cost | Frequency | Delay | Sprint | Status |
|---|---|---|---|---|---|---|
| **Alpaca Market Data** | Prices (OHLCV) | Free (IEX) | Daily | ~minutes after close | 1b | ✅ Primary |
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
| **Massive (ex Polygon)** | Short Interest / Volume | Free | Daily | T+1 | 9.5c | ✅ Active |

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
- **Feed:** `iex` (free tier)
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
**API Endpoint:** `https://api.massive.com/stocks/v1/short-volume`
**Docs:** https://massive.com/docs (formerly https://polygon.io/docs)
**Status:** ✅ Implemented (Sprint 9.5c)

> **Note:** Polygon.io rebranded to Massive in October 2025. The old `api.polygon.io` base URL still works in parallel, but new implementations should use `api.massive.com`.

### What It Delivers
Daily short volume data per ticker from FINRA:
- `short_volume` – shares sold short that day
- `total_volume` – total consolidated volume
- `short_volume_ratio` – short_volume / total_volume

### Configuration
- **API Key:** Free tier at https://massive.com (registration required)
- **Rate Limit:** 5 requests/minute (free tier) → 12s between tickers
- **~765 tickers in ~2.5 hours** per daily run
- **Schedule:** Daily 04:45 CET (after Options IV)
- **Auth:** API key as query parameter `apiKey={key}`

### Feature Signals
- `short_volume_ratio_5d` – 5-day average short volume ratio
- `short_volume_ratio_20d` – 20-day average short volume ratio
- `short_volume_change_20d` – 20-day momentum change in short ratio

### Limitations
- **Free tier rate limit** makes full universe scan slow (~2.5h)
- **T+1 delay** – data available next trading day
- **Short volume ≠ short interest** – volume is daily flow, interest is outstanding positions (bi-monthly FINRA data, separate endpoint)

---

## 11. Not Yet Implemented – Ideas for Later

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
