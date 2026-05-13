# DATABASE.md – Database Schema

> Complete database schema reference for the Signal Warehouse.
> All tables reside in the `signals` schema within the `broker_data` database.
>
> See also: [INDEX.md](INDEX.md) · [ARCHITECTURE.md](ARCHITECTURE.md)

**Last updated:** May 2026

---

## Schema Organization

All tables reside in the `signals` schema. This allows clean permission management and future separation if additional schemas (e.g., `trading`, `analysis`) are needed.

```sql
CREATE SCHEMA IF NOT EXISTS signals;
SET search_path TO signals, public;
```

---

## Layer 1: Raw Data (append-only, immutable)

### `signals.universe`
Dynamic ticker universe. Every ticker ever discovered through a signal remains here.

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
  added_by        VARCHAR(50),        -- 'sp500', 'nasdaq100', 'ark_etf', 'manual'
  is_active       BOOLEAN DEFAULT TRUE,
  last_seen       DATE,
  index_membership VARCHAR(20)[],     -- {sp500, nasdaq100}
  metadata        JSONB
);

CREATE INDEX idx_universe_active ON signals.universe(is_active);
```

### `signals.ticker_blacklist`
Self-learning blacklist for non-equity tickers (ETFs, funds, etc.). Used as O(1) filter during onboarding.

```sql
CREATE TABLE signals.ticker_blacklist (
  ticker          VARCHAR(20) PRIMARY KEY,
  reason          VARCHAR(200),         -- e.g. 'quoteType=ETF', 'manual'
  quote_type      VARCHAR(50),          -- yfinance quoteType (ETF, MUTUALFUND, INDEX, ...)
  detected_by     VARCHAR(100),         -- Detection source
  detected_at     TIMESTAMP DEFAULT NOW()
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
  source          VARCHAR(50),        -- 'alpaca', 'yfinance'
  is_extrapolated BOOLEAN DEFAULT FALSE,
  fetched_at      TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (ticker, trade_date)
);

CREATE INDEX idx_prices_date ON signals.prices_daily(trade_date);
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
SEC Form 4 insider transactions.

```sql
CREATE TABLE signals.insider_trades (
  id              BIGSERIAL PRIMARY KEY,
  ticker          VARCHAR(20),
  company_name    VARCHAR(200),
  cik             VARCHAR(20),           -- SEC Company ID
  insider_name    VARCHAR(200),
  insider_title   VARCHAR(200),
  transaction_date DATE,
  filing_date     DATE,
  transaction_type VARCHAR(20),          -- 'P' (Purchase), 'S' (Sale), ...
  shares          NUMERIC(20,4),
  price_per_share NUMERIC(16,4),
  total_value     NUMERIC(20,2),
  shares_owned_after NUMERIC(20,4),
  is_derivative   BOOLEAN DEFAULT FALSE, -- True for options/warrants
  form4_url       TEXT,
  raw_data        JSONB,                 -- Complete XML/JSON for audit
  fetched_at      TIMESTAMP DEFAULT NOW(),
  UNIQUE (cik, insider_name, transaction_date, transaction_type, shares, price_per_share)
);

CREATE INDEX idx_insider_ticker_date ON signals.insider_trades(ticker, transaction_date);
CREATE INDEX idx_insider_filing_date ON signals.insider_trades(filing_date);
```

### `signals.form13f_holdings`
Quarterly institutional holdings (SEC Form 13F).

```sql
CREATE TABLE signals.form13f_holdings (
  id              BIGSERIAL PRIMARY KEY,
  filer_name      VARCHAR(200),
  filer_cik       VARCHAR(20),
  report_period   DATE,                  -- End of quarter
  filing_date     DATE,
  ticker          VARCHAR(20),
  cusip           VARCHAR(20),
  shares          NUMERIC(20,4),
  market_value    NUMERIC(20,2),
  put_call        VARCHAR(10),
  source_url      TEXT,
  fetched_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_13f_ticker ON signals.form13f_holdings(ticker);
CREATE INDEX idx_13f_filer_period ON signals.form13f_holdings(filer_cik, report_period);
```

### `signals.politician_trades`
US politician trades (Senate eFD).

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
  owner           VARCHAR(50),           -- 'Self', 'Spouse', 'Joint', 'Child'
  asset_description TEXT,
  comment         TEXT,
  source_url      TEXT,
  raw_data        JSONB,
  fetched_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_politician_ticker ON signals.politician_trades(ticker);
CREATE INDEX idx_politician_date ON signals.politician_trades(transaction_date);
CREATE UNIQUE INDEX uq_politician_trade_dedup ON signals.politician_trades(
  politician_name, ticker, transaction_date, transaction_type, amount_range
);
```

### `signals.fundamentals_snapshot`
Fundamental data per ticker, weekly snapshot (Sundays via yfinance).

```sql
CREATE TABLE signals.fundamentals_snapshot (
  ticker          VARCHAR(20) REFERENCES signals.universe(ticker),
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
  dividend_yield  NUMERIC(10,6),
  beta            NUMERIC(10,4),
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
  UNIQUE (ticker, firm, rating_date, action)
);

CREATE INDEX idx_analyst_ticker ON signals.analyst_ratings(ticker);
CREATE INDEX idx_analyst_rating_date ON signals.analyst_ratings(rating_date);
```

### `signals.earnings_calendar`
Earnings dates and results.

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
  fetched_at      TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (ticker, earnings_date)
);
```

### `signals.news_articles`
Financial news articles from Alpaca News API. Raw layer – collected daily.

```sql
CREATE TABLE signals.news_articles (
  id              BIGSERIAL PRIMARY KEY,
  article_id      VARCHAR(100) UNIQUE,           -- Source-specific unique ID
  headline        TEXT NOT NULL,
  summary         TEXT,
  source          VARCHAR(100),                  -- e.g. 'benzinga', 'reuters'
  author          VARCHAR(200),
  published_at    TIMESTAMP NOT NULL,
  article_url     TEXT,
  symbols         VARCHAR(20)[],                 -- PostgreSQL ARRAY (0..N tickers)
  is_global       BOOLEAN DEFAULT FALSE,         -- True for macro/market news
  fetched_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_news_published ON signals.news_articles(published_at);
CREATE INDEX idx_news_symbols ON signals.news_articles USING GIN(symbols);
```

### `signals.news_sentiment`
Sentiment scores per article × ticker × model. Derived from FinBERT (later Haiku).

```sql
CREATE TABLE signals.news_sentiment (
  id              BIGSERIAL PRIMARY KEY,
  article_id      BIGINT REFERENCES signals.news_articles(id),
  ticker          VARCHAR(20),                   -- NULL for global articles
  sentiment_label VARCHAR(20) NOT NULL,          -- 'positive', 'negative', 'neutral'
  sentiment_score NUMERIC(6,4) NOT NULL,         -- -1.0 to +1.0
  confidence      NUMERIC(6,4),                  -- Model confidence
  model_version   VARCHAR(50) NOT NULL,          -- e.g. 'finbert-v1'
  scored_at       TIMESTAMP DEFAULT NOW(),
  UNIQUE (article_id, ticker, model_version)
);

CREATE INDEX idx_sentiment_ticker_date ON signals.news_sentiment(ticker, scored_at);
CREATE INDEX idx_sentiment_model ON signals.news_sentiment(model_version);
```

---

## Layer 2: Derived Data (computed, reproducible)

### `signals.ark_deltas`
Daily changes in ARK holdings. Only real portfolio movements – `unchanged` positions are not stored.

```sql
CREATE TABLE signals.ark_deltas (
  delta_date      DATE NOT NULL,
  etf_ticker      VARCHAR(10) NOT NULL,
  ticker          VARCHAR(20) NOT NULL,
  delta_type      VARCHAR(20),            -- 'new_position', 'closed', 'increased', 'decreased'
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
  computed_at     TIMESTAMP DEFAULT NOW(),
  UNIQUE (ticker, cluster_start)           -- Migration 017: UPSERT dedup
);
```

### `signals.technical_indicators`
Technical indicators per ticker and day.

```sql
CREATE TABLE signals.technical_indicators (
  ticker          VARCHAR(20),
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
```

---

## Layer 3: Analysis (Feature Store + Backtests)

### `signals.feature_snapshots` ⭐
**The heart of the project.** Daily feature vector per ticker, aggregated from all raw and derived data layers. This table serves as training data for ML models. Wide table design (~83 columns) avoids JOINs during training.

> **Design:** All feature columns are nullable. Features self-activate when sufficient data exists. No imputation at the feature store level — the ML stage decides how to handle NULLs.

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
  
  -- ═══ Sentiment Features (Sprint 8c) ═══
  sentiment_avg_7d NUMERIC(10,4),        -- Avg sentiment score (7-day rolling)
  sentiment_avg_30d NUMERIC(10,4),       -- Avg sentiment score (30-day rolling)
  sentiment_momentum NUMERIC(10,4),      -- 7d avg - 30d avg (trend indicator)
  sentiment_neg_count_7d INTEGER,        -- Negative articles in last 7 days
  sentiment_article_count_7d INTEGER,    -- Total articles in last 7 days
  market_sentiment_7d NUMERIC(10,4),     -- Global market sentiment (no ticker)
  
  -- ═══ TARGET VARIABLES (backfilled retrospectively) ═══
  return_1d NUMERIC(10,6),               -- 1-day forward return
  return_5d NUMERIC(10,6),               -- 5-day forward return
  return_20d NUMERIC(10,6),              -- 20-day forward return
  return_60d NUMERIC(10,6),              -- 60-day forward return
  
  computed_at TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (snapshot_date, ticker)
);

CREATE INDEX idx_features_date ON signals.feature_snapshots(snapshot_date);
CREATE INDEX idx_features_ticker ON signals.feature_snapshots(ticker);
```

### `signals.collection_log`
Audit log for all collector runs.

```sql
CREATE TABLE signals.collection_log (
  id              BIGSERIAL PRIMARY KEY,
  collector_name  VARCHAR(100),
  started_at      TIMESTAMP,
  finished_at     TIMESTAMP,
  status          VARCHAR(20),            -- 'success', 'partial', 'failed'
  records_fetched INTEGER,
  records_written INTEGER,
  errors          JSONB,
  log_lines       JSONB,                  -- Captured WARNING/ERROR + INFO lines
  notes           TEXT
);
```

---

## Migrations

Alembic migrations are stored in `src/alembic/versions/`. Current state: migrations 001–020.

| Migration | Description |
|---|---|
| 001 | Schema `signals` |
| 002 | Table `universe` |
| 003 | Tables `prices_daily`, `collection_log` |
| 004 | Table `ark_holdings` |
| 005 | Table `ark_deltas` |
| 006 | Tables `insider_trades`, `insider_clusters` |
| 007 | Table `form13f_holdings` |
| 008 | Table `politician_trades` |
| 009 | Table `fundamentals_snapshot` |
| 010 | Table `analyst_ratings` |
| 011 | Table `earnings_calendar` |
| 012 | Table `technical_indicators` |
| 013 | Dividend yield normalization (fix /100) |
| 014 | `collection_log.log_lines` JSONB column |
| 015 | Table `ticker_blacklist` |
| 016 | `universe.index_membership` array |
| 017 | `insider_clusters` unique constraint + dedup |
| 018 | Table `feature_snapshots` (Sprint 8) |
| 019 | Tables `news_articles` + `news_sentiment` (Sprint 8c) |
| 020 | Add 6 sentiment columns to `feature_snapshots` (Sprint 8c) |

