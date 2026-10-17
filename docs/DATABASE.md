# DATABASE.md – Database Schema

> Complete database schema reference for the Signal Warehouse.
> All tables reside in the `signals` schema within the `broker_data` database.
>
> See also: [INDEX.md](INDEX.md) · [ARCHITECTURE.md](ARCHITECTURE.md)

**Last updated:** October 2026 (migrations 001–036)

---

## Schema Organization

All tables reside in the `signals` schema. This allows clean permission management and future separation if additional schemas (e.g., `trading`, `analysis`) are needed.

```sql
CREATE SCHEMA IF NOT EXISTS signals;
SET search_path TO signals, public;
```

**Conventions**

- The ORM (`src/trading_signals/db/base.py`) sets `MetaData(schema="signals")` with a naming convention (`ix_…`, `uq_…`, `ck_<table>_<name>`, `fk_…`, `pk_<table>`). Constraint names created by hand-written migrations are listed explicitly below.
- `BIGSERIAL` below = `BigInteger` primary key with `autoincrement=True` in the migration.
- `TIMESTAMP` = `timestamp without time zone`; `TIMESTAMPTZ` = `timestamp with time zone`. The `TIMESTAMPTZ` columns of `collection_log`, `news_articles`, `news_sentiment`, `ticker_blacklist` and `stage2_reviews` are mapped with the ORM type `TZDateTime`: naive datetimes written by the app are interpreted as Europe/Berlin wall clock, values are read back as naive Europe/Berlin wall clock (migration 031). `insider_trades.acceptance_datetime` and `cusip_map.resolved_at` use plain `DateTime(timezone=True)`.

---

## Layer 1: Raw Data (append-only, immutable)

### `signals.universe`
Dynamic ticker universe. Every ticker ever discovered through a signal remains here (tickers are never deleted, only marked inactive).

```sql
CREATE TABLE signals.universe (
  ticker          VARCHAR(20) PRIMARY KEY,
  company_name    VARCHAR(200),
  cusip           VARCHAR(20),
  isin            VARCHAR(20),
  exchange        VARCHAR(20),        -- NYSE, NASDAQ, etc.
  currency        VARCHAR(3),
  country         VARCHAR(2),
  sector          VARCHAR(100),
  industry        VARCHAR(100),
  added_date      DATE NOT NULL,
  added_by        VARCHAR(50),        -- 'sp500', 'nasdaq100', 'ark_etf', 'manual'; 'sp500_history' = inactive row of a
                                      --   former S&P 500 member (repair step 2b, applied 2026-10-17: 71 rows, 3 active =
                                      --   current members SKYD/TWLO/VYLR)
  is_active       BOOLEAN DEFAULT TRUE,
  last_seen       DATE,
  index_membership VARCHAR(20)[],     -- {sp500, nasdaq100} (migration 005)
  metadata        JSONB               -- ORM attribute: metadata_json
);

CREATE INDEX idx_universe_active ON signals.universe(is_active);
```

### `signals.ticker_blacklist`
Self-learning blacklist for non-equity tickers (ETFs, funds, etc.). Used as O(1) filter during onboarding.

```sql
CREATE TABLE signals.ticker_blacklist (
  ticker          VARCHAR(20) PRIMARY KEY,
  reason          VARCHAR(50) NOT NULL,   -- e.g. 'quoteType=ETF', 'manual'
  quote_type      VARCHAR(20),            -- yfinance quoteType (ETF, MUTUALFUND, INDEX, ...)
  source          VARCHAR(50),            -- Detection source
  detected_at     TIMESTAMPTZ DEFAULT NOW()  -- TIMESTAMP until migration 031
);
```

### `signals.prices_daily`
Daily OHLCV data.

```sql
CREATE TABLE signals.prices_daily (
  ticker          VARCHAR(20) REFERENCES signals.universe(ticker),
  trade_date      DATE NOT NULL,
  open            NUMERIC(16,4),
  high            NUMERIC(16,4),
  low             NUMERIC(16,4),
  close           NUMERIC(16,4),
  adj_close       NUMERIC(16,4),      -- = close for Alpaca (adjustment=all)
  volume          BIGINT,
  source          VARCHAR(50) DEFAULT 'yfinance',  -- 'alpaca', 'yfinance'; 'alpaca_backfill' = SIP backfill of former
                                                   --   S&P 500 members (repair step 2b, applied 2026-10-17: 44,495 rows,
                                                   --   76 tickers incl. PARA and 1,555 rows overwritten from IEX)
  is_extrapolated BOOLEAN DEFAULT FALSE,           -- gap-filled row (not a real bar)
  fetched_at      TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (ticker, trade_date)
);

CREATE INDEX idx_prices_date ON signals.prices_daily(trade_date);
-- Partial index (migration 003 only, not declared in the ORM)
CREATE INDEX idx_prices_extrapolated ON signals.prices_daily(is_extrapolated)
  WHERE is_extrapolated = TRUE;
```

### `signals.ark_holdings`
Daily snapshots of ARK ETF holdings.

```sql
CREATE TABLE signals.ark_holdings (
  snapshot_date   DATE NOT NULL,
  etf_ticker      VARCHAR(10) NOT NULL,
  ticker          VARCHAR(20) NOT NULL,
  company_name    VARCHAR(200),
  cusip           VARCHAR(20),
  shares          NUMERIC(20,4),
  market_value    NUMERIC(20,2),
  weight_pct      NUMERIC(8,4),
  weight_rank     INTEGER,
  share_price     NUMERIC(16,4),
  source          VARCHAR(50) DEFAULT 'arkfunds.io',
  fetched_at      TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (snapshot_date, etf_ticker, ticker)
);

CREATE INDEX idx_ark_ticker ON signals.ark_holdings(ticker);
CREATE INDEX idx_ark_date ON signals.ark_holdings(snapshot_date);
```

### `signals.insider_trades`
SEC Form 4 insider transactions. One row per transaction (not per reporting owner): joint filings carry the first owner in `insider_name`/`insider_cik`, `owner_count` holds the number of owners and `raw_data['reporting_owners']` lists all of them.

```sql
CREATE TABLE signals.insider_trades (
  id              BIGSERIAL PRIMARY KEY,
  ticker          VARCHAR(20),
  company_name    VARCHAR(200),
  cik             VARCHAR(20),           -- SEC Company ID
  insider_name    VARCHAR(200),
  insider_title   VARCHAR(200),
  insider_cik     VARCHAR(20),           -- migration 029
  owner_count     SMALLINT,              -- migration 029
  transaction_date DATE,
  filing_date     DATE,
  acceptance_datetime TIMESTAMPTZ,       -- SEC acceptanceDateTime (migration 029)
  accession_number VARCHAR(25),          -- migration 029 (back-filled from form4_url for legacy rows)
  form_type       VARCHAR(10),           -- migration 029
  row_index       INTEGER,               -- 0-based position in (non)derivativeTable; NULL for legacy rows (029)
  transaction_type VARCHAR(20),          -- 'P' (Purchase), 'S' (Sale), ...
  shares          NUMERIC(20,4),
  price_per_share NUMERIC(16,4),
  total_value     NUMERIC(20,2),
  shares_owned_after NUMERIC(20,4),
  is_derivative   BOOLEAN DEFAULT FALSE, -- True for options/warrants
  form4_url       TEXT,
  raw_data        JSONB,                 -- Complete XML/JSON for audit
  fetched_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_insider_ticker_date ON signals.insider_trades(ticker, transaction_date);
CREATE INDEX idx_insider_filing_date ON signals.insider_trades(filing_date);
-- Dedup key since migration 029 (replaces UNIQUE uq_insider_trade_dedup
-- (cik, insider_name, transaction_date, transaction_type, shares, price_per_share))
CREATE UNIQUE INDEX uq_insider_trade_filing_row
  ON signals.insider_trades(accession_number, is_derivative, row_index)
  WHERE accession_number IS NOT NULL;
```

### `signals.form13f_holdings`
Quarterly institutional holdings (SEC Form 13F). Key: one row per (filer, report period, CUSIP, put/call); multiple infotable lines for the same key are summed by the collector. `put_call = 'SH'` marks plain share/principal positions, `'PUT'`/`'CALL'` option positions – consumers counting holders should filter `put_call = 'SH'`.

```sql
CREATE TABLE signals.form13f_holdings (
  id              BIGSERIAL PRIMARY KEY,
  filer_name      VARCHAR(200),
  filer_cik       VARCHAR(20),
  report_period   DATE,                  -- End of quarter
  filing_date     DATE,
  ticker          VARCHAR(20),           -- resolved via cusip_map (migration 032)
  cusip           VARCHAR(20),
  shares          NUMERIC(20,4),
  market_value    NUMERIC(20,2),         -- always whole US dollars (data fix in migration 029)
  put_call        VARCHAR(10) NOT NULL DEFAULT 'SH',  -- NOT NULL since migration 029
  accession_number VARCHAR(25),          -- migration 029 ('legacy' if not derivable for old rows)
  form_type       VARCHAR(12),           -- migration 029
  amendment_type  VARCHAR(20),           -- migration 029
  source_url      TEXT,
  fetched_at      TIMESTAMP DEFAULT NOW(),
  CONSTRAINT uq_13f_holding_key UNIQUE (filer_cik, report_period, cusip, put_call)
);

CREATE INDEX idx_13f_ticker ON signals.form13f_holdings(ticker);
CREATE INDEX idx_13f_ticker_period ON signals.form13f_holdings(ticker, report_period);  -- migration 032
CREATE INDEX idx_13f_filer_period ON signals.form13f_holdings(filer_cik, report_period);
```

### `signals.cusip_map` (Migration 032)
CUSIP → ticker lookup cache for 13F holdings (13F infotables identify securities only by CUSIP). Filled from local sources (ARK holdings, universe) or the OpenFIGI API; the 13F collector and `scripts/repair/form13f_resolve_tickers.py` fill `form13f_holdings.ticker` from it. Rows with `ticker` NULL are negative results and are retried after `cusip_resolver.NEGATIVE_TTL_DAYS`. Lookup table without a date dimension → not part of the data-retention job.

```sql
CREATE TABLE signals.cusip_map (
  cusip           VARCHAR(12) PRIMARY KEY,
  ticker          VARCHAR(20),            -- NULL = negative lookup result
  name            VARCHAR(200),
  exch_code       VARCHAR(10),
  security_type   VARCHAR(50),
  source          VARCHAR(20) NOT NULL,   -- 'ark_holdings' | 'universe' | 'openfigi'
  resolved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_cusip_map_ticker ON signals.cusip_map(ticker);
```

### `signals.politician_trades`
US politician trades (Senate eFD + House Clerk).

```sql
CREATE TABLE signals.politician_trades (
  id              BIGSERIAL PRIMARY KEY,
  politician_name VARCHAR(200),
  chamber         VARCHAR(20),           -- 'Senate', 'House'
  party           VARCHAR(20),
  state           VARCHAR(2),
  ticker          VARCHAR(20),
  transaction_date DATE,
  disclosure_date DATE,
  transaction_type VARCHAR(20),          -- 'Purchase', 'Sale'
  amount_range    VARCHAR(50),           -- '$1,001 - $15,000' etc.
  owner           VARCHAR(50) NOT NULL DEFAULT 'Self',  -- 'Self', 'Spouse', 'Joint', 'Child' (NOT NULL since 029)
  asset_description TEXT,
  comment         TEXT,
  source_url      TEXT,
  raw_data        JSONB,
  fetched_at      TIMESTAMP DEFAULT NOW(),
  -- owner added to the key in migration 029 (identical Self/Spouse trades are distinct)
  CONSTRAINT uq_politician_trade_dedup UNIQUE (
    politician_name, ticker, transaction_date, transaction_type, amount_range, owner
  )
);

CREATE INDEX idx_politician_ticker ON signals.politician_trades(ticker);
CREATE INDEX idx_politician_date ON signals.politician_trades(transaction_date);
```

### `signals.fundamentals_snapshot`
Fundamental data per ticker, weekly snapshot (Sundays via yfinance).

```sql
CREATE TABLE signals.fundamentals_snapshot (
  ticker          VARCHAR(20),           -- no FK to universe (neither migration 009 nor ORM)
  snapshot_date   DATE NOT NULL,
  market_cap      NUMERIC(24,2),
  pe_ratio        NUMERIC(16,4),
  forward_pe      NUMERIC(16,4),
  ps_ratio        NUMERIC(16,4),
  pb_ratio        NUMERIC(16,4),
  ev_ebitda       NUMERIC(16,4),
  profit_margin   NUMERIC(10,6),
  operating_margin NUMERIC(10,6),
  return_on_equity NUMERIC(10,6),
  revenue_ttm     NUMERIC(20,2),
  revenue_growth_yoy NUMERIC(10,6),
  eps_ttm         NUMERIC(16,4),
  eps_growth_yoy  NUMERIC(10,6),
  debt_to_equity  NUMERIC(16,4),
  current_ratio   NUMERIC(16,4),
  dividend_yield  NUMERIC(10,6),         -- decimal form (0.0092 = 0.92 %), fixed in migration 013
  beta            NUMERIC(10,4),
  -- Analyst consensus price targets (migration 016, yfinance analyst_price_targets)
  target_price_low    NUMERIC(16,4),
  target_price_mean   NUMERIC(16,4),
  target_price_median NUMERIC(16,4),
  target_price_high   NUMERIC(16,4),
  most_recent_quarter DATE,              -- fiscal quarter end of the TTM figures (migration 029)
  fetched_at      TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (ticker, snapshot_date)
);
```

### `signals.analyst_ratings`
Analyst upgrades/downgrades (individual firm-level entries via yfinance).

```sql
CREATE TABLE signals.analyst_ratings (
  id              BIGSERIAL PRIMARY KEY,
  ticker          VARCHAR(20),
  firm            VARCHAR(200),
  analyst         VARCHAR(200),
  rating_date     DATE,
  rating_new      VARCHAR(50),           -- 'Buy', 'Hold', 'Sell'
  rating_old      VARCHAR(50),
  price_target_new NUMERIC(16,4),
  price_target_old NUMERIC(16,4),
  action          VARCHAR(50),           -- 'up', 'down', 'main', 'init', 'reit'
  raw_data        JSONB,
  fetched_at      TIMESTAMP DEFAULT NOW(),
  CONSTRAINT uq_analyst_rating_dedup UNIQUE (ticker, firm, rating_date, action)
);

CREATE INDEX idx_analyst_ticker ON signals.analyst_ratings(ticker);
CREATE INDEX idx_analyst_rating_date ON signals.analyst_ratings(rating_date);
```

### `signals.earnings_calendar`
Earnings dates and results. Excluded from the data-retention job (historical value).

```sql
CREATE TABLE signals.earnings_calendar (
  ticker          VARCHAR(20),
  earnings_date   DATE,
  time_of_day     VARCHAR(20),           -- 'BMO' (Before Market Open), 'AMC'
  eps_estimate    NUMERIC(16,4),
  eps_actual      NUMERIC(16,4),
  revenue_estimate NUMERIC(20,2),
  revenue_actual  NUMERIC(20,2),
  surprise_pct    NUMERIC(10,4),
  fetched_at      TIMESTAMP DEFAULT NOW(),  -- time of the last insert/update
  first_seen      TIMESTAMP DEFAULT NOW(),  -- set on insert only (migration 029)
  last_seen       TIMESTAMP DEFAULT NOW(),  -- bumped on every run still reporting the date (029)
  PRIMARY KEY (ticker, earnings_date)
);
```

### `signals.estimates_snapshot`
Daily point-in-time snapshot of analyst consensus estimates, EPS revisions, and revenue forecasts.
*Sprint 9.5a. Most time-critical data source — rolling 90-day window at Yahoo.*

```sql
CREATE TABLE signals.estimates_snapshot (
  id              BIGSERIAL PRIMARY KEY,         -- constraint name pk_estimates_snapshot
  ticker          VARCHAR(20) NOT NULL,
  as_of           DATE NOT NULL,                 -- Point-in-time: last completed NYSE session at fetch time
                                                 -- (rows written before migration 029 carry the Berlin fetch date)
  period          VARCHAR(10) NOT NULL,           -- '0q', '+1q', '0y', '+1y'
  -- EPS Consensus
  eps_avg         NUMERIC(16,4),
  eps_low         NUMERIC(16,4),
  eps_high        NUMERIC(16,4),
  eps_n_analysts  INTEGER,
  eps_year_ago    NUMERIC(16,4),
  eps_growth      NUMERIC(10,6),
  -- EPS Trend (rolling 90-day window)
  eps_current     NUMERIC(16,4),
  eps_7d_ago      NUMERIC(16,4),
  eps_30d_ago     NUMERIC(16,4),
  eps_60d_ago     NUMERIC(16,4),
  eps_90d_ago     NUMERIC(16,4),
  -- Revision Counts
  rev_up_7d       INTEGER,
  rev_up_30d      INTEGER,
  rev_down_7d     INTEGER,
  rev_down_30d    INTEGER,
  -- Revenue Consensus
  revenue_avg     NUMERIC(20,2),
  revenue_low     NUMERIC(20,2),
  revenue_high    NUMERIC(20,2),
  revenue_n_analysts INTEGER,
  revenue_year_ago NUMERIC(20,2),
  revenue_growth  NUMERIC(10,6),
  -- Metadata
  source          VARCHAR(50) NOT NULL DEFAULT 'yfinance',
  raw             JSONB,                         -- Complete raw response (future-proofing)
  fetched_at      TIMESTAMP DEFAULT NOW(),
  CONSTRAINT uq_estimates_snapshot_dedup UNIQUE (ticker, as_of, period, source)
);

CREATE INDEX idx_estimates_ticker ON signals.estimates_snapshot(ticker);
CREATE INDEX idx_estimates_as_of ON signals.estimates_snapshot(as_of);
```

### `signals.index_membership`
Historical index constituent membership with time intervals. Tracks when
each ticker joined and left an index (S&P 500, Nasdaq 100). Enables
point-in-time universe queries to prevent survivorship bias
(`UniverseManager.get_universe_as_of(d)`: `valid_from <= d < valid_to`).

- **Primary source:** Wikipedia change tables "Historical components of the S&P 500" and "Historical components of the Nasdaq-100" (effective dates). The former "Selected changes" table on "List of S&P 500 companies" no longer exists; the parser marks a result without removals as incomplete and seeding refuses it.
- **Cross-check only:** GitHub `fja05680/sp500` "(Updated)" CSV (snapshot dates, lags the index by weeks); not a seeding source.
- **Ongoing updates:** monthly `IndexSyncer` (`index_sync`) opens/closes intervals at the effective change date and re-keys the open interval when a member is renamed ([SCHEDULER.md](SCHEDULER.md)).
- **valid_to = NULL** means ticker is currently an active member; `valid_to` is the first day that is **not** a member (interval `[valid_from, valid_to)`).
- **Rename convention:** a ticker rename is one continuous interval under the **current** symbol (e.g. `META` from 2021-01-01, `DAY` including the CDAY years); old symbols get no interval. A real exit ends the interval at the effective removal date; the company's prices are stored under the same current symbol. Intervals of long-standing members start at 2021-01-01 (not at `data_start_date()`).
- **`source` values:** `wikipedia`, `github_csv`, `index_sync`, `initial_seed`, `manual` (events that no change table lists, e.g. the Paramount Global exit / Paramount Skydance entry of 2025-08-08).
- **State before the S&P 500 repair (2026-10-16):** 506 `sp500` rows = 416 `initial_seed` members (from 2021-01-01) + 87 `wikipedia` + 3 `index_sync` additions, only 3 closed intervals (TAP, TTD, BLDR, dated 10 days late). Point-in-time S&P 500 members: 426 (2022-01-03), 441 (2023), 456 (2024), 472 (2025), 490 (2026) instead of 503–505; 92 real exits inside the data window were missing. The Nasdaq-100 rows are not affected.
- **State after the repair (applied 2026-10-17, step 2a; features rebuilt on it the same day, `feature_snapshots` 676,953 rows, every point-in-time member has a snapshot on every session):** 610 `sp500` intervals = 503 open + 107 closed (92 exits in the window incl. 3 merger successors, 15 exits before 2021-10-01), 503–506 members on every session since 2021-10-01. `scripts/repair/rebuild_sp500_membership.py` replaces only the `sp500` rows (backup CSV first); runbook and expected numbers: [reports/2026-10-16_sp500_survivorship_inventory.md](reports/2026-10-16_sp500_survivorship_inventory.md), decision: [DECISIONS_DATA.md](DECISIONS_DATA.md) 2026-10-16.

```sql
CREATE TABLE signals.index_membership (
  id              BIGSERIAL PRIMARY KEY,
  ticker          VARCHAR(20) NOT NULL,
  index_name      VARCHAR(20) NOT NULL,  -- 'sp500', 'nasdaq100'
  valid_from      DATE NOT NULL,
  valid_to        DATE,                  -- NULL = currently active
  reason          VARCHAR(200),          -- 'Delisted', 'Merger', etc.
  replaced_by     VARCHAR(20),           -- Successor ticker
  source          VARCHAR(50) NOT NULL,  -- 'wikipedia', 'github_csv', 'index_sync', 'initial_seed', 'manual'
  CONSTRAINT uq_membership_ticker_index_from UNIQUE (ticker, index_name, valid_from)
);

CREATE INDEX ix_membership_lookup ON signals.index_membership(index_name, valid_from, valid_to);
CREATE INDEX ix_membership_ticker ON signals.index_membership(ticker);
```

### `signals.news_articles`
Financial news articles from Alpaca News API. Raw layer – collected daily.

```sql
CREATE TABLE signals.news_articles (
  id              BIGSERIAL PRIMARY KEY,
  article_id      VARCHAR(100),                  -- Source-specific unique ID
  headline        TEXT NOT NULL,
  summary         TEXT,
  source          VARCHAR(100),                  -- e.g. 'benzinga', 'reuters'
  author          VARCHAR(200),
  published_at    TIMESTAMPTZ NOT NULL,          -- TIMESTAMP until migration 031
  article_url     TEXT,
  symbols         VARCHAR(20)[],                 -- PostgreSQL ARRAY (0..N tickers)
  is_global       BOOLEAN DEFAULT FALSE,         -- True for macro/market news
  fetched_at      TIMESTAMPTZ DEFAULT NOW(),     -- TIMESTAMP until migration 031
  CONSTRAINT uq_news_articles_article_id UNIQUE (article_id)
);

CREATE INDEX idx_news_published ON signals.news_articles(published_at);
CREATE INDEX idx_news_symbols ON signals.news_articles USING GIN(symbols);
```

### `signals.news_sentiment`
Sentiment scores per article × ticker × model. Derived from FinBERT (later Haiku). A multi-ticker article generates one row per mentioned ticker.

```sql
CREATE TABLE signals.news_sentiment (
  id              BIGSERIAL PRIMARY KEY,
  article_id      BIGINT NOT NULL,
  ticker          VARCHAR(20),                   -- NULL for global articles
  sentiment_label VARCHAR(20) NOT NULL,          -- 'positive', 'negative', 'neutral'
  sentiment_score NUMERIC(6,4) NOT NULL,         -- -1.0 to +1.0
  confidence      NUMERIC(6,4),                  -- Model confidence
  model_version   VARCHAR(50) NOT NULL,          -- e.g. 'finbert-v1'
  scored_at       TIMESTAMPTZ DEFAULT NOW(),     -- TIMESTAMP until migration 031
  -- ON DELETE CASCADE since migration 031 (retention deletes old articles)
  CONSTRAINT fk_news_sentiment_article_id FOREIGN KEY (article_id)
    REFERENCES signals.news_articles(id) ON DELETE CASCADE,
  CONSTRAINT uq_news_sentiment_article_ticker_model UNIQUE (article_id, ticker, model_version)
);

CREATE INDEX idx_sentiment_ticker_date ON signals.news_sentiment(ticker, scored_at);
CREATE INDEX idx_sentiment_model ON signals.news_sentiment(model_version);
```

### `signals.macro_series` (Migration 024, Sprint 9.5b D1)
Daily observations of macroeconomic indicators from FRED (DGS2/DGS10, BAMLH0A0HYM2, VIXCLS, DTWEXBGS, T10YIE). `as_of` records the fetch date because FRED revises values retroactively.

```sql
CREATE TABLE signals.macro_series (
  id              BIGSERIAL PRIMARY KEY,
  series_id       VARCHAR(30) NOT NULL,          -- FRED series ID
  obs_date        DATE NOT NULL,
  value           NUMERIC(16,6),
  source          VARCHAR(20) NOT NULL DEFAULT 'fred',
  as_of           DATE NOT NULL,                 -- when we fetched it
  fetched_at      TIMESTAMP DEFAULT NOW(),
  CONSTRAINT uq_macro_series_dedup UNIQUE (series_id, obs_date)
);

CREATE INDEX idx_macro_series_id ON signals.macro_series(series_id);
CREATE INDEX idx_macro_obs_date ON signals.macro_series(obs_date);
```

### `signals.options_iv_snapshot` (Migration 026, Sprint 9.5b D3)
Daily implied-volatility snapshot per ticker from the Alpaca Options API (IV rank / skew / term-structure features). Historic rows written before migration 029 carry the Berlin fetch date (next calendar day) as `snapshot_date`; they were intentionally not relabelled. New rows use the last completed NYSE session.

```sql
CREATE TABLE signals.options_iv_snapshot (
  ticker          VARCHAR(20) NOT NULL,
  snapshot_date   DATE NOT NULL,
  atm_iv_30d      NUMERIC(8,4),          -- ATM IV, nearest monthly expiry (annualized)
  atm_iv_60d      NUMERIC(8,4),          -- ATM IV ~60d expiry
  skew_25d        NUMERIC(8,4),          -- IV 25-delta put − IV 25-delta call
  term_slope      NUMERIC(8,4),          -- atm_iv_60d − atm_iv_30d
  total_oi_call   BIGINT,
  total_oi_put    BIGINT,
  put_call_oi     NUMERIC(8,4),          -- Put/Call OI ratio
  PRIMARY KEY (ticker, snapshot_date)
);

CREATE INDEX idx_options_iv_ticker_date ON signals.options_iv_snapshot(ticker, snapshot_date);
```

---

### `signals.short_volume` (Sprint 9.5c B5)
Daily short volume data from FINRA via Massive API (formerly Polygon). Written by `short_interest_collector` (target date = last completed NYSE session).

```sql
CREATE TABLE signals.short_volume (
  id              BIGSERIAL PRIMARY KEY,
  ticker          VARCHAR(20) NOT NULL,
  trade_date      DATE NOT NULL,
  short_volume    BIGINT,           -- shares sold short that day
  total_volume    BIGINT,           -- total consolidated volume
  short_volume_ratio NUMERIC(6,4), -- short_volume / total_volume
  source          VARCHAR(50) DEFAULT 'massive',
  fetched_at      TIMESTAMP DEFAULT NOW(),
  CONSTRAINT uq_short_volume_dedup UNIQUE (ticker, trade_date)
);

CREATE INDEX idx_short_volume_ticker ON signals.short_volume(ticker);
CREATE INDEX idx_short_volume_date ON signals.short_volume(trade_date);
```

### `signals.short_interest` (Sprint 9.5c B5)
Bi-monthly FINRA short interest settlement data (outstanding short positions per settlement date). Written by the weekly `short_interest_positions_collector` (`collectors/short_interest_positions_collector.py`, collector name `short_interest_positions`, since 2026-10-16; Massive `GET /stocks/v1/short-interest`, upsert on `(ticker, settlement_date, source)`, so FINRA revisions overwrite). Initial history via `scripts/repair/collect_short_interest.py`; details: [DATA_SOURCES.md](DATA_SOURCES.md) §10.

> [!NOTE]
> The table exists since migration 027 but stayed empty until the collector was added (2026-10-16; filled the same day by the initial backfill: 94,370 rows, 120 settlement dates 2021-10-15 to 2026-09-30). `short_interest_collector.py` writes only `short_volume` (daily short *volume*) – the two are different datasets. Rows cover all universe tickers incl. inactive ones (survivorship). `days_to_cover` and `avg_daily_volume` are NULL when the API reports the "no volume" sentinel (`days_to_cover = 999.99`, `avg_daily_volume = 0`). The payload has **no publication date**: consumers must apply an availability lag (feature pipeline: 9 NYSE sessions, see the feature columns below and `derived/short_interest_features.py`). The ORM classes `ShortVolume`/`ShortInterest` live in `db/models/short_interest.py` but are not re-exported from `db/models/__init__.py`; the dashboard reads `short_interest` (`api/routes/dashboard.py`), `data_retention` prunes it by `settlement_date`.

```sql
CREATE TABLE signals.short_interest (
  id              BIGSERIAL PRIMARY KEY,
  ticker          VARCHAR(20) NOT NULL,
  settlement_date DATE NOT NULL,
  short_interest  BIGINT,           -- total shares short
  avg_daily_volume BIGINT,
  days_to_cover   NUMERIC(10,4),
  source          VARCHAR(50) DEFAULT 'massive',
  fetched_at      TIMESTAMP DEFAULT NOW(),
  CONSTRAINT uq_short_interest_dedup UNIQUE (ticker, settlement_date, source)
);

CREATE INDEX idx_short_interest_ticker ON signals.short_interest(ticker);
```

---

## Layer 2: Derived Data (computed, reproducible)

### `signals.ark_deltas`
Daily changes in ARK holdings. Only real portfolio movements – `unchanged` positions are not stored. Since migration 030, new/closed positions carry signed full-size deltas (`new_position`: `+shares_curr`/`+weight_curr`, `closed`: `−shares_prev`/`−weight_prev`).

```sql
CREATE TABLE signals.ark_deltas (
  delta_date      DATE NOT NULL,
  etf_ticker      VARCHAR(10) NOT NULL,
  ticker          VARCHAR(20) NOT NULL,
  delta_type      VARCHAR(20) NOT NULL,   -- 'new_position', 'closed', 'increased', 'decreased'
  shares_prev     NUMERIC(20,4),
  shares_curr     NUMERIC(20,4),
  shares_delta    NUMERIC(20,4),
  weight_prev     NUMERIC(8,4),
  weight_curr     NUMERIC(8,4),
  weight_delta    NUMERIC(8,4),
  computed_at     TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (delta_date, etf_ticker, ticker)
);

CREATE INDEX idx_ark_deltas_ticker ON signals.ark_deltas(ticker);
```

### `signals.insider_clusters`
Cluster detection for insider trades (multiple insiders buying within a short timeframe).

```sql
CREATE TABLE signals.insider_clusters (
  id              BIGSERIAL PRIMARY KEY,
  ticker          VARCHAR(20),
  cluster_start   DATE,
  cluster_end     DATE,
  n_insiders      INTEGER,
  n_buys          INTEGER,
  n_sells         INTEGER,
  total_buy_value NUMERIC(20,2),
  total_sell_value NUMERIC(20,2),
  cluster_score   NUMERIC(10,4),          -- Computed score
  known_date      DATE,                   -- date the cluster became public (point-in-time), migration 029
  computed_at     TIMESTAMP DEFAULT NOW(),
  CONSTRAINT uq_insider_cluster_ticker_start UNIQUE (ticker, cluster_start)  -- Migration 017: UPSERT dedup
);

CREATE INDEX idx_insider_clusters_ticker ON signals.insider_clusters(ticker);
```

### `signals.technical_indicators`
Technical indicators per ticker and day. Since migration 030, rows on gap-filled (`is_extrapolated`) price rows are deleted / not computed.

```sql
CREATE TABLE signals.technical_indicators (
  ticker          VARCHAR(20),            -- no FK to universe (neither migration 012 nor ORM, aligned 2026-10-16)
  trade_date      DATE,
  sma_20          NUMERIC(16,4),
  sma_50          NUMERIC(16,4),
  sma_200         NUMERIC(16,4),
  ema_12          NUMERIC(16,4),
  ema_26          NUMERIC(16,4),
  rsi_14          NUMERIC(10,4),
  macd            NUMERIC(16,4),
  macd_signal     NUMERIC(16,4),
  macd_histogram  NUMERIC(16,4),
  bollinger_upper NUMERIC(16,4),
  bollinger_lower NUMERIC(16,4),
  atr_14          NUMERIC(16,4),
  volume_sma_20   NUMERIC(20,2),
  relative_strength_spy NUMERIC(10,4),
  PRIMARY KEY (ticker, trade_date)
);

CREATE INDEX idx_technical_indicators_date ON signals.technical_indicators(trade_date);
```

---

## Layer 3: Analysis (Feature Store + Backtests)

### `signals.feature_snapshots` ⭐
**The heart of the project.** Daily feature vector per ticker, aggregated from all raw and derived data layers. This table serves as training data for ML models. Wide table design avoids JOINs during training: **108 columns** = 2 key + 97 features + 5 return targets + 2 trade labels + 2 metadata columns (`FEATURE_COLUMNS` / `TARGET_COLUMNS` / `LABEL_COLUMNS` / `META_COLUMNS` in `db/models/features.py`).

> **Design:** All feature columns are nullable. Features self-activate when sufficient data exists. No imputation at the feature store level — the ML stage decides how to handle NULLs.

> **NYSE sessions only:** migration 030 deleted rows on weekends and exchange holidays (weekdays without a real SPY bar inside SPY's price history).

```sql
CREATE TABLE signals.feature_snapshots (
  snapshot_date   DATE NOT NULL,
  ticker          VARCHAR(20) NOT NULL,
  
  -- ═══ ARK Features (point-in-time) ═══
  ark_in_etf_count INTEGER,               -- How many ARK ETFs hold this ticker?
  ark_total_weight NUMERIC(10,4),         -- Sum of weight across all ARK ETFs
  ark_weight_delta_1d NUMERIC(10,4),      -- Weight change vs yesterday
  ark_weight_delta_5d NUMERIC(10,4),      -- Weight change over 5 trading days
  ark_weight_delta_20d NUMERIC(10,4),     -- Weight change over 20 trading days
  ark_conviction_score NUMERIC(10,4),     -- Composite: n_etfs * (1+weight_trend) * (1+streak)
  ark_multi_etf_signal BOOLEAN,           -- True if ≥2 ETFs hold this ticker
  
  -- ═══ ARK Temporal Features ═══
  ark_increase_days_10d INTEGER,          -- Days with delta_type='increased' in last 10d
  ark_increase_days_20d INTEGER,          -- Days with delta_type='increased' in last 20d
  ark_conviction_streak INTEGER,          -- Consecutive days with increase (current streak)
  ark_weight_trend_20d NUMERIC(10,6),     -- Linear regression slope of total_weight over 20d
  
  -- ═══ Insider Features (point-in-time) ═══
  insider_net_buy_count_30d INTEGER,      -- Buys minus sells in last 30 days
  insider_buy_value_30d NUMERIC(20,2),    -- Total buy value in last 30 days
  insider_cluster_active BOOLEAN,         -- Active cluster in last 21 days?
  insider_cluster_score NUMERIC(10,4),    -- Score of most recent active cluster
  
  -- ═══ Insider Temporal Features ═══
  cluster_count_30d INTEGER,              -- Number of clusters in last 30 days
  cluster_count_60d INTEGER,              -- Number of clusters in last 60 days
  cluster_score_sum_60d NUMERIC(10,4),    -- Sum of cluster scores in last 60 days
  days_since_last_cluster INTEGER,        -- Calendar days since most recent cluster_end
  insider_buy_ratio_30d NUMERIC(6,4),     -- Sprint 9.5b E2 (migration 024)
  insider_buy_ratio_90d NUMERIC(6,4),     -- Sprint 9.5b E2 (migration 024)
  
  -- ═══ Analyst Features (point-in-time) ═══
  analyst_rating_score NUMERIC(10,4),     -- Consensus score (1=sell, 5=buy)
  analyst_upgrades_30d INTEGER,           -- Number of upgrades in last 30 days
  analyst_price_target_upside NUMERIC(10,4), -- (median_target / current_price) - 1
  
  -- ═══ Analyst Temporal Features ═══
  analyst_downgrades_30d INTEGER,         -- Number of downgrades in last 30 days
  analyst_net_sentiment_30d INTEGER,      -- upgrades - downgrades in 30 days
  analyst_net_sentiment_60d INTEGER,      -- upgrades - downgrades in 60 days
  analyst_upgrade_streak INTEGER,         -- Consecutive days with ≥1 upgrade, 0 downgrades
  
  -- ═══ Politician Features (dual-date: disclosure + transaction) ═══
  -- Disclosure-based (when information became public)
  politician_buy_count_60d_disclosure INTEGER,   -- Buys disclosed in last 60 days
  politician_distinct_90d_disclosure INTEGER,    -- Distinct politicians disclosed in 90d
  -- Transaction-based (when trade actually occurred)
  politician_buy_count_60d_transaction INTEGER,  -- Buys with transaction_date in last 60d
  politician_distinct_90d_transaction INTEGER,   -- Distinct politicians traded in 90d
  
  -- ═══ 13F Features ═══
  form13f_top_holder_count INTEGER,       -- How many top-20 filers hold this ticker
  form13f_new_positions_count INTEGER,    -- New positions in latest reporting period
  form13f_exited_positions_count INTEGER, -- Sprint 9.5b E1 (migration 024)
  form13f_holder_delta_qoq NUMERIC(10,4), -- Sprint 9.5b E1 (migration 024)
  
  -- ═══ Fundamentals (point-in-time) ═══
  pe_ratio NUMERIC(16,4),
  forward_pe NUMERIC(16,4),
  ps_ratio NUMERIC(16,4),
  revenue_growth_yoy NUMERIC(10,6),
  profit_margin NUMERIC(10,6),
  debt_to_equity NUMERIC(16,4),
  
  -- ═══ Fundamentals Temporal Features ═══
  pe_trend_4w NUMERIC(10,6),             -- Regression slope of pe_ratio over 4 weeks
  margin_trend_4w NUMERIC(10,6),         -- Regression slope of profit_margin over 4 weeks
  
  -- ═══ Technical Indicators ═══
  price_vs_sma50 NUMERIC(10,4),          -- (price / sma50) - 1
  price_vs_sma200 NUMERIC(10,4),         -- (price / sma200) - 1
  rsi_14 NUMERIC(10,4),
  relative_strength_spy NUMERIC(10,4),   -- Excess return vs SPY (20d)
  volume_ratio_20d NUMERIC(10,4),        -- today_volume / sma_volume_20d
  atr_14_pct NUMERIC(10,4),             -- ATR as % of price
  
  -- ═══ Earnings Features ═══
  earnings_days_until INTEGER,            -- Days until next earnings call
  consecutive_beats INTEGER,             -- Consecutive quarters EPS > estimate
  surprise_trend_3q NUMERIC(10,4),       -- Avg surprise_pct of last 3 quarters
  sue_last NUMERIC(10,4),                -- Sprint 9.5b B2 (migration 024)
  days_since_last_earnings INTEGER,      -- Sprint 9.5b B2 (migration 024)
  
  -- ═══ Sentiment Features (Sprint 8c, migration 020) ═══
  sentiment_avg_7d NUMERIC(6,4),         -- Avg sentiment score (7-day rolling)
  sentiment_avg_30d NUMERIC(6,4),        -- Avg sentiment score (30-day rolling)
  sentiment_momentum NUMERIC(6,4),       -- 7d avg - 30d avg (trend indicator)
  sentiment_neg_count_7d INTEGER,        -- Negative articles in last 7 days
  sentiment_article_count_7d INTEGER,    -- Total articles in last 7 days
  market_sentiment_7d NUMERIC(6,4),      -- Global market sentiment (no ticker)
  news_volume_ratio_7d NUMERIC(10,4),    -- Sprint 9.5b E3 (migration 024)
  
  -- ═══ Liquidity Features (Sprint 9.5b E4, migration 024) ═══
  dollar_volume_20d NUMERIC(20,0),
  amihud_illiquidity_20d NUMERIC(16,6),
  
  -- ═══ Macro Features (market-wide, Sprint 9.5b D1, migration 024) ═══
  macro_yield_spread NUMERIC(10,4),
  macro_vix NUMERIC(10,2),
  macro_vix_regime INTEGER,
  macro_hy_spread NUMERIC(10,4),
  macro_dollar_index NUMERIC(10,2),
  macro_inflation_expectation NUMERIC(10,4),
  
  -- ═══ Market Breadth (Sprint 9.5b D2, migration 024) ═══
  breadth_advance_decline NUMERIC(6,4),
  breadth_pct_above_sma50 NUMERIC(6,4),
  
  -- ═══ Sector-Relative (Sprint 9.5b B4, migrations 024/025) ═══
  sector_relative_return_20d NUMERIC(10,6),
  sector_relative_momentum NUMERIC(10,6),
  
  -- ═══ Short Volume Features (Sprint 9.5c B5, migration 027) ═══
  short_volume_ratio_5d NUMERIC(10,4),
  short_volume_ratio_20d NUMERIC(10,4),
  short_volume_change_20d NUMERIC(10,4),
  
  -- ═══ Short Interest Features (FINRA settlements, migration 036, derived/short_interest_features.py) ═══
  short_interest_days_to_cover NUMERIC(10,4),  -- days_to_cover of the newest usable settlement
  short_interest_change_1m NUMERIC(10,4),      -- short_interest(newest) / short_interest(2 usable settlements earlier) - 1
  -- usable for day d: settlement_date <= 9th NYSE session before d; newest older than 40 days -> NULL;
  -- change_1m: comparison settlement max 45 days before the newest, short_interest > 0
  
  -- ═══ Options IV Features (migration 028, from options_iv_snapshot) ═══
  options_iv_atm_30d NUMERIC(8,4),
  options_iv_skew_25d NUMERIC(8,4),
  options_iv_term_slope NUMERIC(8,4),
  options_iv_put_call_oi NUMERIC(8,4),
  
  -- ═══ Estimates Features (migration 028, from estimates_snapshot) ═══
  eps_revision_pct_30d NUMERIC(10,6),
  eps_revision_pct_90d NUMERIC(10,6),
  revenue_revision_pct_30d NUMERIC(10,6),
  eps_revisions_net_7d INTEGER,
  eps_revisions_net_30d INTEGER,
  
  -- ═══ SHORT-TERM PRICE FEATURES (migration 034, derived/short_term_features.py) ═══
  st_return_1d, st_return_5d, st_gap, st_close_location, st_dist_52w_high,
  st_rsi_2, st_bollinger_pctb, st_signed_volume_shock,
  st_earnings_reaction NUMERIC(12,6),    -- all nine NUMERIC(12,6); definitions: concept 2026-10-06 §7.1

  -- ═══ TARGET VARIABLES (backfilled retrospectively) ═══
  -- Definition since migration 030 (FEATURE_VERSION >= 2026.10-1):
  --   return_h = close(d+h) / open(d+1) - 1 on the NYSE calendar
  -- (migration 030 reset all old values to NULL; target_backfill recomputes them)
  return_1d NUMERIC(10,6),               -- 1-session forward return
  return_5d NUMERIC(10,6),               -- 5-session forward return
  return_20d NUMERIC(10,6),              -- 20-session forward return
  return_60d NUMERIC(10,6),              -- 60-session forward return
  -- Barrier trade label (migration 033): entry open d+1, TP +1 % / SL -2 % / max 14 sessions, net of 0.05 % costs
  return_barrier_14d NUMERIC(10,6),      -- net trade return (model target of the feature analysis)
  barrier_outcome SMALLINT,              -- 1 = take profit, -1 = stop, 0 = time stop
  barrier_ambiguous BOOLEAN,             -- TP and SL touched on the same day (resolved via OHLC rule)
  
  -- ═══ Metadata ═══
  computed_at TIMESTAMP DEFAULT NOW(),
  feature_version VARCHAR(20),           -- derived.feature_pipeline.FEATURE_VERSION (migration 030);
                                         -- NULL = computed before 2026.10-1
  PRIMARY KEY (snapshot_date, ticker)
);

CREATE INDEX idx_features_date ON signals.feature_snapshots(snapshot_date);
CREATE INDEX idx_features_ticker ON signals.feature_snapshots(ticker);
```

### `signals.analysis_reports` (Migration 021)
Monthly feature-analysis results (Spearman correlations, Random-Forest and LASSO importance, hypothesis tests, consensus ranking) plus the HTML report served by the API.

```sql
CREATE TABLE signals.analysis_reports (
  id                       SERIAL PRIMARY KEY,   -- Integer autoincrement
  report_date              DATE NOT NULL UNIQUE,
  snapshot_count           INTEGER NOT NULL,
  ticker_count             INTEGER NOT NULL,
  date_range_start         DATE NOT NULL,
  date_range_end           DATE NOT NULL,
  feature_correlations     JSONB,     -- {feature: {return_1d: {rho, pvalue}, ...}}
  feature_importance_rf    JSONB,     -- {feature: {importance, std}}
  feature_importance_lasso JSONB,     -- {feature: coefficient}
  hypothesis_results       JSONB,     -- {H1: {verdict, pvalue, effect_size, detail}, ...}
  consensus_features       JSONB,     -- [{feature, spearman_rank, rf_rank, lasso_rank}, ...]
  html_report              TEXT,
  computation_time_seconds DOUBLE PRECISION,
  computed_at              TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_analysis_reports_date ON signals.analysis_reports(report_date);
```

### `signals.stage2_reviews` / `signals.stage2_decisions` (Migration 035)
Forward test of stage 2 (Claude broker skill), concept 2026-10-06 §7.7. Filled by the nightly step `stage2_review` from `context_packs/<date>/decisions.yaml` (format: [STAGE2_DECISIONS.md](STAGE2_DECISIONS.md)). Trade results are **not** stored – the evaluation joins the barrier label of `feature_snapshots` (ticker, `session_date`).

```sql
CREATE TABLE signals.stage2_reviews (
  session_date    DATE PRIMARY KEY,         -- pack day d (as_of of the context pack)
  decided_at      TIMESTAMPTZ NOT NULL,     -- from the file
  file_mtime      TIMESTAMPTZ NOT NULL,     -- last write of decisions.yaml
  on_time         BOOLEAN NOT NULL,         -- decided_at AND file_mtime < open of session d+1
  n_buy           SMALLINT NOT NULL,
  n_no_entry      SMALLINT NOT NULL,
  content_sha256  VARCHAR(64) NOT NULL,     -- unchanged files are skipped
  source_file     VARCHAR(300) NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE signals.stage2_decisions (
  id            BIGSERIAL PRIMARY KEY,
  session_date  DATE NOT NULL REFERENCES signals.stage2_reviews(session_date) ON DELETE CASCADE,
  ticker        VARCHAR(20) NOT NULL,
  action        VARCHAR(10) NOT NULL,
  pack_rank     SMALLINT,                   -- NN of NN_TICKER.md, NULL if not in the pack
  reason        TEXT,
  limit_price   NUMERIC(12,4),              -- informational only
  target_pct    NUMERIC(8,4),               -- informational only
  stop_pct      NUMERIC(8,4),               -- informational only
  CONSTRAINT ck_stage2_decisions_action CHECK (action IN ('buy', 'no_entry')),
  CONSTRAINT uq_stage2_session_ticker UNIQUE (session_date, ticker)
);
```

A changed `decisions.yaml` replaces the stored day (review + decisions); its new `file_mtime` decides `on_time`.

### `signals.collection_log`
Audit log for all collector runs (one row per run).

```sql
CREATE TABLE signals.collection_log (
  id              BIGSERIAL PRIMARY KEY,
  collector_name  VARCHAR(100),
  started_at      TIMESTAMPTZ,            -- TIMESTAMP until migration 031 (old values = Berlin wall clock)
  finished_at     TIMESTAMPTZ,            -- TIMESTAMP until migration 031
  status          VARCHAR(20),            -- 'success', 'partial', 'failed' (see utils/job_status.py)
  records_fetched INTEGER,
  records_written INTEGER,
  gaps_detected   INTEGER DEFAULT 0,
  gaps_repaired   INTEGER DEFAULT 0,
  gaps_extrapolated INTEGER DEFAULT 0,
  errors          JSONB,
  log_lines       JSONB,                  -- Captured WARNING/ERROR + INFO lines (migration 014)
  notes           TEXT
);

-- Migration 031 (dashboard / health / retention look-ups)
CREATE INDEX ix_collection_log_collector_started ON signals.collection_log(collector_name, started_at DESC);
CREATE INDEX ix_collection_log_started_at ON signals.collection_log(started_at);
```

---

## Data Retention

Decision 2026-10-05: the system keeps exactly the last **20 calendar quarters** (5 years; `DATA_RETENTION_QUARTERS` in `src/trading_signals/utils/retention.py`). The cutoff is the first day of the quarter 20 quarters back (`quarter_cutoff()`, e.g. today 2026-09-22 → 2021-07-01); rows strictly older than the cutoff are deleted. Collectors and backfills must not store data older than `data_start_date()` (= the same cutoff).

**`data_retention` job** – weekly, Sunday 03:00 Europe/Berlin (`scheduler/setup.py`). `retention_statements()` in `src/trading_signals/scheduler/jobs.py` issues `DELETE … WHERE <column> < cutoff` per table; each table runs in its own savepoint + commit, so one failing table does not undo the others (job status `partial`/`failed`).

| Table | Date column used |
|---|---|
| `news_sentiment` | via its article: `article_id IN (SELECT id FROM news_articles WHERE published_at < cutoff)` (runs first) |
| `news_articles` | `published_at` (FK `ON DELETE CASCADE` from `news_sentiment`, migration 031) |
| `prices_daily` | `trade_date` |
| `technical_indicators` | `trade_date` |
| `insider_trades` | `filing_date` (not `transaction_date`, which contains bogus years) |
| `insider_clusters` | `cluster_start` |
| `analyst_ratings` | `rating_date` |
| `ark_holdings` | `snapshot_date` |
| `ark_deltas` | `delta_date` |
| `fundamentals_snapshot` | `snapshot_date` |
| `form13f_holdings` | `filing_date` |
| `politician_trades` | `disclosure_date` |
| `feature_snapshots` | `snapshot_date` |
| `options_iv_snapshot` | `snapshot_date` |
| `estimates_snapshot` | `as_of` |
| `short_volume` | `trade_date` |
| `short_interest` | `settlement_date` |
| `macro_series` | `obs_date` |
| `analysis_reports` | `report_date` |

**Not covered by `data_retention`:**

- `earnings_calendar` – excluded on purpose (historical value).
- `collection_log` – own **`log_retention`** job, daily 03:30 Europe/Berlin: deletes rows with `started_at` older than 90 days (`LOG_RETENTION_DAYS`) and marks orphaned running rows as failed.
- `universe`, `ticker_blacklist`, `index_membership` – excluded (reference data).
- `cusip_map` – lookup table without a date dimension.
- `stage2_reviews`, `stage2_decisions` – not listed in `retention_statements()` (no retention).

> [!NOTE]
> Rows with a NULL value in the retention date column (e.g. `insider_trades.filing_date IS NULL`) are never matched by `< cutoff` and therefore never deleted by this job.

---

## Migrations

Alembic migrations are stored in `src/alembic/versions/`. Current state: migrations 001–036 (linear chain, head = 036; production at 036 since 2026-10-16).

| Migration | Description |
|---|---|
| 001 | Schema `signals` |
| 002 | Table `universe` (+ `idx_universe_active`) |
| 003 | Tables `prices_daily` (FK → `universe`, partial index `idx_prices_extrapolated`) and `collection_log` (incl. `gaps_*` counters) |
| 004 | Tables `ark_holdings` and `ark_deltas` |
| 005 | `universe.index_membership` `VARCHAR(20)[]` column |
| 006 | Tables `insider_trades` (unique `uq_insider_trade_dedup`) and `insider_clusters` |
| 007 | Table `form13f_holdings` (unique `uq_13f_holding_dedup` on filer_cik, report_period, cusip) |
| 008 | Table `politician_trades` (unique `uq_politician_trade_dedup`) |
| 009 | Table `fundamentals_snapshot` |
| 010 | Table `analyst_ratings` (unique `uq_analyst_rating_dedup`) |
| 011 | Table `earnings_calendar` |
| 012 | Table `technical_indicators` (+ `idx_technical_indicators_date`) |
| 013 | Data fix: `fundamentals_snapshot.dividend_yield` values > 0.25 divided by 100 |
| 014 | `collection_log.log_lines` JSONB column |
| 015 | Table `ticker_blacklist` |
| 016 | `fundamentals_snapshot.target_price_low/mean/median/high` (analyst consensus price targets) |
| 017 | `insider_clusters`: duplicates removed (keep max id), unique `uq_insider_cluster_ticker_start` (ticker, cluster_start) |
| 018 | Table `feature_snapshots` (Sprint 8: ARK, insider, analyst, politician, 13F, fundamentals, technical, earnings features + `return_1d/5d/20d/60d`) |
| 019 | Tables `news_articles` (GIN index on `symbols`) + `news_sentiment` (FK `fk_news_sentiment_article_id`) (Sprint 8c) |
| 020 | 6 sentiment columns on `feature_snapshots` (Sprint 8c) |
| 021 | Table `analysis_reports` (monthly feature analysis) |
| 022 | Table `estimates_snapshot` (Sprint 9.5a) |
| 023 | Table `index_membership` (Sprint 9.5a A1, survivorship bias) |
| 024 | Table `macro_series` (FRED) + 19 `feature_snapshots` columns: `macro_*` (6), `sue_last`, `days_since_last_earnings`, `news_volume_ratio_7d`, `dollar_volume_20d`, `amihud_illiquidity_20d`, `insider_buy_ratio_30d/90d`, `form13f_exited_positions_count`, `form13f_holder_delta_qoq`, `breadth_*` (2), `sector_relative_return_20d`, `sector_relative_momentum` |
| 025 | Adds `sector_relative_return_20d`, `sector_relative_momentum` to `feature_snapshots` (Sprint 9.5b B4) – same two columns as in 024; idempotent since 2026-10-16, see note below |
| 026 | Table `options_iv_snapshot` (Sprint 9.5b D3) |
| 027 | Tables `short_volume` + `short_interest`; `feature_snapshots.short_volume_ratio_5d/20d`, `short_volume_change_20d` (Sprint 9.5c B5) |
| 028 | `feature_snapshots`: options-IV features `options_iv_atm_30d/skew_25d/term_slope/put_call_oi` and estimates features `eps_revision_pct_30d/90d`, `revenue_revision_pct_30d`, `eps_revisions_net_7d/30d` |
| 029 | Collector data integrity: `form13f_holdings` + `accession_number/form_type/amendment_type`, market_value ÷1000 for filings ≥ 2023-01-03, `put_call` NOT NULL DEFAULT 'SH', unique key → `uq_13f_holding_key` (+ put_call); `insider_trades` + `accession_number/form_type/row_index/acceptance_datetime/insider_cik/owner_count`, exact duplicates removed, `uq_insider_trade_dedup` → partial unique index `uq_insider_trade_filing_row`; `insider_clusters.known_date`; `politician_trades.owner` normalised, NOT NULL DEFAULT 'Self', added to `uq_politician_trade_dedup`; `earnings_calendar.first_seen/last_seen`; `fundamentals_snapshot.most_recent_quarter` |
| 030 | Feature-store integrity: `feature_snapshots.feature_version`; rows on weekends/exchange holidays deleted; `return_*` targets reset to NULL (new definition close(d+h)/open(d+1) − 1); `technical_indicators` rows on extrapolated prices deleted; signed full-size `ark_deltas` for new/closed positions |
| 031 | Ops integrity: `collection_log.started_at/finished_at`, `news_articles.published_at/fetched_at`, `news_sentiment.scored_at`, `ticker_blacklist.detected_at` → `timestamptz`; `fk_news_sentiment_article_id` → `ON DELETE CASCADE`; indexes `ix_collection_log_collector_started`, `ix_collection_log_started_at` |
| 032 | Table `cusip_map` (CUSIP → ticker cache for 13F) + index `idx_13f_ticker_period` on `form13f_holdings(ticker, report_period)` |
| 033 | Barrier trade label on `feature_snapshots`: `return_barrier_14d`, `barrier_outcome`, `barrier_ambiguous` |
| 034 | Nine short-term price features `st_*` (NUMERIC(12,6)) on `feature_snapshots` (concept 2026-10-06 §7.1) |
| 035 | Tables `stage2_reviews` + `stage2_decisions` (FK `ON DELETE CASCADE`, check `ck_stage2_decisions_action`, unique `uq_stage2_session_ticker`; stage-2 forward test, concept 2026-10-06 §7.7) |
| 036 | `feature_snapshots.short_interest_days_to_cover` + `short_interest_change_1m` (NUMERIC(10,4), FINRA settlement features; `FEATURE_VERSION` 2026.10-3). History: `scripts/repair/backfill_short_interest_features.py` (only these two columns) |

> [!NOTE]
> Migration 024 was extended after production had applied it (commit `81446a4` added `sector_relative_return_20d` / `sector_relative_momentum`), so 025 (`32168a0`) created the two columns in production. On an empty database both added them and `alembic upgrade head` failed at 025. **Fixed 2026-10-16:** 025 uses `ADD COLUMN IF NOT EXISTS` and its downgrade is a no-op (024's downgrade drops the columns). Verified on a throwaway `postgres:18`: fresh `upgrade head` → 035, round trip `025 → 023 → head`, and the production history (old 024 without the columns → 025 creates them). No effect on databases already at 025 or later.

**Migrations and ORM are aligned** (2026-10-16): the live schema is defined by the migrations (no `metadata.create_all` in `src/` or `scripts/`); the ORM models mirror it exactly – nullability, server defaults, all indexes (incl. the partial `idx_prices_extrapolated` and `ix_collection_log_collector_started` on `started_at DESC`), column comments. `technical_indicators.ticker` has **no** FK to `universe` (none was ever migrated; derived table). Gate: `alembic check` on a fresh `upgrade head` – `src/alembic/env.py` compares the `signals` schema only (`include_schemas` + `include_name`) and server defaults (`compare_server_default`); expected output "No new upgrade operations detected." A schema change therefore always means a new migration **and** the matching ORM change in the same commit.
