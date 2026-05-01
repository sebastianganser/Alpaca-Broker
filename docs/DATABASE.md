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
**The heart of the project.** Daily feature vector per ticker, aggregated from all raw data layers. This table will serve as training data for ML models.

```sql
CREATE TABLE signals.feature_snapshots (
  snapshot_date   DATE NOT NULL,
  ticker          VARCHAR(20) NOT NULL,
  
  -- ARK Features
  ark_in_etf_count INTEGER,               -- How many ARK ETFs hold this ticker?
  ark_total_weight NUMERIC(10,4),         -- Sum of weight across all ARK ETFs
  ark_weight_delta_1d NUMERIC(10,4),
  ark_weight_delta_5d NUMERIC(10,4),
  ark_weight_delta_20d NUMERIC(10,4),
  ark_conviction_score NUMERIC(10,4),
  ark_multi_etf_signal BOOLEAN,
  
  -- Insider Features
  insider_net_buy_count_30d INTEGER,      -- Buys minus sells
  insider_buy_value_30d NUMERIC(20,2),
  insider_cluster_active BOOLEAN,
  insider_cluster_score NUMERIC(10,4),
  
  -- 13F Features  
  form13f_top_holder_count INTEGER,       -- How many top holders hold the ticker
  form13f_new_positions_count INTEGER,    -- New positions in last reporting period
  
  -- Fundamentals
  pe_ratio NUMERIC(16,4),
  ps_ratio NUMERIC(16,4),
  revenue_growth_yoy NUMERIC(10,6),
  profit_margin NUMERIC(10,6),
  debt_to_equity NUMERIC(16,4),
  
  -- Technical Indicators
  price_vs_sma50 NUMERIC(10,4),           -- (price / sma50) - 1
  price_vs_sma200 NUMERIC(10,4),
  rsi_14 NUMERIC(10,4),
  relative_strength_spy NUMERIC(10,4),
  volume_ratio_20d NUMERIC(10,4),
  atr_14_pct NUMERIC(10,4),
  
  -- Analyst
  analyst_rating_score NUMERIC(10,4),     -- Consensus score
  analyst_upgrades_30d INTEGER,
  analyst_price_target_upside NUMERIC(10,4),
  
  -- Context
  earnings_days_until INTEGER,            -- Days until next earnings call
  
  -- TARGETS (ML target variables, backfilled retrospectively)
  return_1d NUMERIC(10,6),
  return_5d NUMERIC(10,6),
  return_20d NUMERIC(10,6),
  return_60d NUMERIC(10,6),
  
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

Alembic migrations are stored in `src/alembic/versions/`. Current state: migrations 001–017.

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
