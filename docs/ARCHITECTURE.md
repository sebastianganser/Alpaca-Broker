# ARCHITECTURE.md – Technische Architektur

> Dieses Dokument beschreibt den technischen Aufbau des Signal Warehouse.
> Änderungen an der Struktur werden hier dokumentiert.

---

## Deployment-Topologie

```
┌─────────────────────────────────────────────────────────────┐
│  Windows-Rechner (Entwicklung + Frontend)                   │
│  ├─ Claude Desktop (Sonnet/Opus für Design, Debugging)      │
│  ├─ VS Code + Claude Code                                   │
│  └─ Git-Client → GitHub                                     │
└────────────────────────┬────────────────────────────────────┘
                         │ SSH / Docker Remote
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Unraid-Server (Produktion)                                 │
│  ✅ LIVE – Container läuft auf 192.168.1.93:8090             │
│                                                             │
│  ┌───────────────────┐    ┌────────────────────────────┐    │
│  │ signal-collector  │    │ postgresql18-alpaca        │    │
│  │ (Python Container)│───▶│ (separater Container)      │    │
│  │                   │    │ DB: broker_data            │    │
│  │ APScheduler +     │    │ Schema: signals            │    │
│  │ FastAPI + React   │    │ User: sebastian            │    │
│  │ • Täglich 22–00h  │    │ Port: 5435                 │    │
│  │ • Nachtslot 01–03 │    │ Volume: /mnt/user/         │    │
│  │ • UI auf :8090    │    │   Datafolder/Broker/       │    │
│  └───────────────────┘    └────────────────────────────┘    │
│                                                             │
│                           ┌────────────────────────────┐    │
│                           │ Alpaca API                 │    │
│                           │ (Paper Trading NUR!)       │    │
│                           │ Endpoint hardcoded check   │    │
│                           └────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Netzwerk:** Der Collector-Container verbindet sich direkt zur bestehenden PostgreSQL-Instanz via Host-IP.
**Backup:** Tägliches pg_dump via Cron auf Unraid, in den bestehenden Backup-Ordner.
**Deployment-Gate:** ✅ Deployment abgeschlossen. System läuft produktiv seit 13.04.2026.

---

## Projektstruktur (Zielbild)

```
Alpaca-Broker/
├── CLAUDE.md                      # Projekt-Einstiegspunkt
├── README.md                      # GitHub-Übersicht
├── docs/                          # Alle Dokumentation
│   ├── ARCHITECTURE.md            # Dieses Dokument
│   ├── ROADMAP.md
│   ├── DATA_SOURCES.md
│   ├── DECISIONS.md
│   └── LEARNINGS.md
├── infra/                         # Deployment
│   ├── docker-compose.yml         # Nur Collector-Service (DB läuft separat)
│   └── Dockerfile.collector
├── src/
│   ├── trading_signals/           # Python-Paket
│   │   ├── __init__.py
│   │   ├── config.py              # Pydantic Settings
│   │   ├── db/                    # Datenbank-Layer
│   │   │   ├── __init__.py
│   │   │   ├── base.py            # SQLAlchemy Base (Schema: signals)
│   │   │   ├── session.py         # Engine + Session-Factory
│   │   │   └── models/            # ORM-Modelle pro Tabelle
│   │   │       ├── universe.py    # ✅ implementiert (644 Ticker)
│   │   │       ├── prices.py      # ✅ Sprint 1
│   │   │       ├── ark.py         # ✅ Sprint 2
│   │   │       ├── insider.py     # ✅ Sprint 3
│   │   │       ├── politicians.py # ✅ Sprint 4
│   │   │       ├── fundamentals.py# ✅ Sprint 5 (3 Modelle)
│   │   │       ├── technical_indicators.py # ✅ Sprint 6
│   │   │       └── features.py    # Sprint 8
│   │   ├── collectors/            # Daten-Sammler (ein Modul pro Quelle)
│   │   │   ├── __init__.py
│   │   │   ├── base.py            # ✅ Abstract BaseCollector
│   │   │   ├── prices_alpaca.py   # ✅ Sprint 1b (primär)
│   │   │   ├── prices_yfinance.py # ✅ Sprint 1 (Fallback)
│   │   │   ├── ark_holdings.py    # ✅ Sprint 2
│   │   │   ├── gap_detector.py    # ✅ Sprint 1
│   │   │   ├── sec_client.py      # ✅ Sprint 3 (SEC API Client)
│   │   │   ├── form4_collector.py # ✅ Sprint 3 (Insider-Trades)
│   │   │   ├── form13f_collector.py # ✅ Sprint 3 (Institutionelle)
│   │   │   ├── disclosure_client.py # ✅ Sprint 4 (Senate eFD)
│   │   │   ├── politician_trades_collector.py # ✅ Sprint 4
│   │   │   ├── yfinance_client.py   # ✅ Sprint 5 (Shared Client)
│   │   │   ├── fundamentals_collector.py # ✅ Sprint 5
│   │   │   ├── analyst_ratings_collector.py # ✅ Sprint 5
│   │   │   ├── earnings_calendar_collector.py # ✅ Sprint 5
│   │   ├── derived/               # Berechnete Features
│   │   │   ├── __init__.py
│   │   │   ├── ark_deltas.py      # ✅ Sprint 2
│   │   │   ├── insider_clusters.py# ✅ Sprint 3
│   │   │   ├── technical_indicators.py # ✅ Sprint 6
│   │   │   └── feature_pipeline.py# Sprint 8
│   │   ├── universe/              # Dynamisches Titel-Universum
│   │   │   ├── __init__.py
│   │   │   ├── manager.py         # ✅ implementiert
│   │   │   ├── alpaca_validator.py # ✅ Alpaca-Validierung
│   │   │   └── index_sync.py      # ✅ Sprint 1b (S&P/Nasdaq sync)
│   │   ├── api/                   # ✅ Sprint 7 (FastAPI Backend)
│   │   │   ├── __init__.py
│   │   │   ├── deps.py           # ✅ DB session + Scheduler dependency injection
│   │   │   ├── job_tracker.py     # ✅ APScheduler event listener (live running status)
│   │   │   ├── schemas.py         # ✅ 21+ Pydantic response schemas
│   │   │   ├── tasks.py           # BackfillManager (Threading)
│   │   │   └── routes/
│   │   │       ├── dashboard.py   # /api/v1/dashboard/summary
│   │   │       ├── universe.py    # /api/v1/universe (paginated)
│   │   │       ├── signals.py     # /api/v1/signals/ark,insider,...
│   │   │       ├── ticker.py      # /api/v1/ticker/{sym}/prices,data-quality,...
│   │   │       └── operations.py  # /api/v1/ops/scheduler,backfill,db
│   │   ├── scheduler/             # Job-Orchestrierung
│   │   │   ├── __init__.py
│   │   │   └── jobs.py            # ✅ 10 Jobs: Prices + ARK + Form4 + 13F + Politicians + yfinance(3) + TA + IndexSync (inkl. Sektor-Enrichment)
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── logging.py         # ✅ implementiert
│   │       └── retry.py           # ✅ implementiert
│   └── alembic/                   # Datenbank-Migrationen (001-012)
│       ├── env.py
│       ├── script.py.mako
│       └── versions/
├── tests/
│   ├── unit/                      # ✅ 303 Tests
│   ├── integration/
│   └── fixtures/
├── scripts/                       # Einmal-Skripte
│   ├── init_universe.py           # ✅ S&P 100 + SPY
│   ├── validate_universe.py       # ✅ Alpaca-Validierung
│   ├── sync_universe_indexes.py   # ✅ S&P 500 + Nasdaq 100
│   ├── enrich_universe_sectors.py # ✅ Sektor/Branche via yfinance
│   └── backfill_prices.py         # ✅ Sprint 6 (Preis-Backfill ab 2021)
├── pyproject.toml                 # uv Paketmanager
├── uv.lock                        # uv Lockfile
├── alembic.ini                    # Alembic-Konfiguration
├── .env.example                   # Env-Template (Credentials)
├── .gitignore
├── .dockerignore                  # ✅ Sprint 7
└── .python-version                # 3.12
frontend/                          # ✅ Sprint 7 (Vite + React SPA)
├── index.html
├── package.json
├── vite.config.ts                 # Proxy -> :8090 (dev)
└── src/
    ├── main.tsx
    ├── App.tsx                    # Router + QueryClient
    ├── Layout.tsx                 # Sidebar (Logo + World Clock) + Outlet
    ├── api.ts                     # Typed API Client (fetch-wrapper)
    ├── index.css                  # Precision Architect Design System
    └── pages/
        ├── DashboardPage.tsx      # Collector-Status, Stats, Health
        ├── UniversePage.tsx       # Filtered/Paginated Ticker Table
        ├── SignalsPage.tsx        # Tabbed: ARK, Insider, Politicians, Analyst
        ├── LogsPage.tsx           # Scheduler-Logs (Tabbed: alle / Fehler)
        ├── SettingsPage.tsx       # Scheduler, Backfill (Prices/TA/Sectors), DB Ops
        └── TickerPage.tsx         # Chart, Indicators, Fundamentals, Data Quality
infra/
├── Dockerfile.collector           # ✅ 3-Stage Build (Node+Python+Runtime)
├── docker-compose.yml             # Port 8090
└── entrypoint.sh                  # DB-Wait + Alembic + CMD
```

---

## Datenbank-Schema

### Schema-Organisation

Alle Tabellen liegen im Schema `signals`. Das erlaubt sauberes Rechte-Management und spätere Trennung, falls wir weitere Schemas (z.B. `trading`, `analysis`) hinzufügen.

```sql
CREATE SCHEMA IF NOT EXISTS signals;
SET search_path TO signals, public;
```

### Layer 1: Raw Data (append-only, heilig)

#### `signals.universe`
Das dynamische Titel-Universum. Jeder Titel, der jemals durch ein Signal aufgenommen wurde, bleibt hier.

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

#### `signals.prices_daily`
Tägliche OHLCV-Daten.

```sql
CREATE TABLE signals.prices_daily (
  ticker          VARCHAR(20) REFERENCES signals.universe(ticker),
  trade_date      DATE NOT NULL,
  open            NUMERIC(16,4),
  high            NUMERIC(16,4),
  low             NUMERIC(16,4),
  close           NUMERIC(16,4),
  adj_close       NUMERIC(16,4),      -- = close bei Alpaca (adjustment=all)
  volume          BIGINT,
  source          VARCHAR(50),        -- 'alpaca', 'yfinance'
  is_extrapolated BOOLEAN DEFAULT FALSE,
  fetched_at      TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (ticker, trade_date)
);

CREATE INDEX idx_prices_date ON signals.prices_daily(trade_date);
```

#### `signals.ark_holdings`
Tägliche Snapshots der ARK-ETF-Holdings.

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

#### `signals.insider_trades`
SEC Form 4 Insider-Transaktionen.

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
  is_derivative   BOOLEAN DEFAULT FALSE, -- True für Options/Warrants
  form4_url       TEXT,
  raw_data        JSONB,                 -- Komplette XML/JSON für Audit
  fetched_at      TIMESTAMP DEFAULT NOW(),
  UNIQUE (cik, insider_name, transaction_date, transaction_type, shares, price_per_share)
);

CREATE INDEX idx_insider_ticker_date ON signals.insider_trades(ticker, transaction_date);
CREATE INDEX idx_insider_filing_date ON signals.insider_trades(filing_date);
```

#### `signals.form13f_holdings`
Quartalsweise institutionelle Holdings (SEC Form 13F).

```sql
CREATE TABLE signals.form13f_holdings (
  id              BIGSERIAL PRIMARY KEY,
  filer_name      VARCHAR(200),
  filer_cik       VARCHAR(20),
  report_period   DATE,                  -- Ende des Quartals
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

#### `signals.politician_trades`
US-Politiker-Trades (Senate eFD).

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

#### `signals.fundamentals_snapshot`
Fundamentaldaten pro Titel, wöchentlicher Snapshot (sonntags via yfinance).

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

#### `signals.analyst_ratings`
Analyst-Upgrades/Downgrades (individuelle Firmen-Level-Einträge via yfinance).

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

#### `signals.earnings_calendar`
Earnings-Termine.

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

### Layer 2: Derived Data (berechnet, neu erzeugbar)

#### `signals.ark_deltas`
Tagesveränderungen in ARK-Holdings.

```sql
CREATE TABLE signals.ark_deltas (
  delta_date      DATE NOT NULL,
  etf_ticker      VARCHAR(10) NOT NULL,
  ticker          VARCHAR(20) NOT NULL,
  shares_prev     NUMERIC(20,4),
  shares_new      NUMERIC(20,4),
  shares_delta    NUMERIC(20,4),
  weight_prev     NUMERIC(8,4),
  weight_new      NUMERIC(8,4),
  weight_delta_bps NUMERIC(12,4),        -- in Basispunkten
  is_new_position BOOLEAN,
  is_closed_position BOOLEAN,
  pct_change      NUMERIC(10,4),          -- % Änderung der Shares
  PRIMARY KEY (delta_date, etf_ticker, ticker)
);
```

#### `signals.insider_clusters`
Cluster-Erkennung bei Insider-Trades (mehrere Insider kaufen zeitnah).

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
  cluster_score   NUMERIC(10,4),          -- Unser berechneter Score
  computed_at     TIMESTAMP DEFAULT NOW()
);
```

#### `signals.technical_indicators`
Technische Indikatoren pro Titel und Tag.

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

### Layer 3: Analysis (Feature Store + Backtests)

#### `signals.feature_snapshots` ⭐
**Das Herz des Projekts.** Täglicher Feature-Vektor pro Titel, wird aus allen Rohdaten-Layers aggregiert. Diese Tabelle ist später das Trainingsmaterial für ML-Modelle.

```sql
CREATE TABLE signals.feature_snapshots (
  snapshot_date   DATE NOT NULL,
  ticker          VARCHAR(20) NOT NULL,
  
  -- ARK Features
  ark_in_etf_count INTEGER,               -- In wie vielen ARK-ETFs ist der Titel?
  ark_total_weight NUMERIC(10,4),         -- Summe Gewichtung über alle ARK-ETFs
  ark_weight_delta_1d NUMERIC(10,4),
  ark_weight_delta_5d NUMERIC(10,4),
  ark_weight_delta_20d NUMERIC(10,4),
  ark_conviction_score NUMERIC(10,4),
  ark_multi_etf_signal BOOLEAN,
  
  -- Insider Features
  insider_net_buy_count_30d INTEGER,      -- Käufe minus Verkäufe
  insider_buy_value_30d NUMERIC(20,2),
  insider_cluster_active BOOLEAN,
  insider_cluster_score NUMERIC(10,4),
  
  -- 13F Features  
  form13f_top_holder_count INTEGER,       -- Wie viele Top-Holder halten den Titel
  form13f_new_positions_count INTEGER,    -- Neue Positionen letzte Reporting-Periode
  
  -- Fundamentals
  pe_ratio NUMERIC(16,4),
  ps_ratio NUMERIC(16,4),
  revenue_growth_yoy NUMERIC(10,6),
  profit_margin NUMERIC(10,6),
  debt_to_equity NUMERIC(16,4),
  
  -- Technische Indikatoren
  price_vs_sma50 NUMERIC(10,4),           -- (price / sma50) - 1
  price_vs_sma200 NUMERIC(10,4),
  rsi_14 NUMERIC(10,4),
  relative_strength_spy NUMERIC(10,4),
  volume_ratio_20d NUMERIC(10,4),
  atr_14_pct NUMERIC(10,4),
  
  -- Analyst
  analyst_rating_score NUMERIC(10,4),     -- Konsens-Score
  analyst_upgrades_30d INTEGER,
  analyst_price_target_upside NUMERIC(10,4),
  
  -- Kontext
  earnings_days_until INTEGER,            -- Tage bis zum nächsten Earnings-Call
  
  -- TARGETS (Zielvariablen für ML, werden nachträglich befüllt)
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

#### `signals.collection_log`
Audit-Log aller Collector-Läufe.

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
  notes           TEXT
);
```

---

## Datenfluss-Diagramm

```
┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐  ┌──────────────┐
│ Alpaca Market  │  │ arkfunds.io    │  │ SEC EDGAR API  │  │ Senate eFD   │  │ yfinance     │
│ Data API ⭐    │  │ (ARK Holdings) │  │ (Form 4/13F)   │  │ (Politician) │  │ (Fund/Rtg/Ea)│
└────────┬───────┘  └────────┬───────┘  └────────┬───────┘  └──────┬───────┘  └──────┬───────┘
         │                   │                   │                 │                 │
         ▼                   ▼                   ▼                 ▼                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                Collectors (Python)                                             │
│  [prices_alpaca] [ark_holdings] [form4] [form13f] [politicians] [fund] [ratings] [earnings]   │
└────────────────────────────────┬───────────────────────────────────────────────────────────────┘
                                 │ INSERT / UPSERT
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│              Raw Layer (append-only / upsert)                                        │
│  prices_daily | ark_holdings | insider_trades | politician_trades                    │
│  fundamentals_snapshot | analyst_ratings | earnings_calendar                          │
└────────────────────────────────┬─────────────────────────────────────────────────────┘
                                 │ SELECT + COMPUTE
                                 ▼
┌─────────────────────────────────────────────────────────┐
│              Derived Layer (recomputable)               │
│  ark_deltas | insider_clusters | technical_indicators   │
└────────────────────────┬────────────────────────────────┘
                         │ AGGREGATE
                         ▼
┌─────────────────────────────────────────────────────────┐
│       Feature Store (feature_snapshots) ⭐              │
│     Ein Feature-Vektor pro Ticker pro Tag               │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
         ┌───────────────┴────────────────┐
         ▼                                ▼
┌──────────────────┐          ┌──────────────────────┐
│ Jupyter Analysis │          │ Scoring (später)     │
│ ML Experiments   │          │ Trading Signals      │
└──────────────────┘          └──────────────────────┘
```

---

## Ausführungs-Zeitplan

**Täglich – Nachtschicht (01:00 MEZ):**

1. `analyst_ratings_collector` – Analyst-Upgrades/Downgrades via yfinance (01:00 MEZ) ✅ Sprint 5

**Täglich – Abendschicht (nach US-EOD):**

2. `prices_alpaca` – OHLCV für das gesamte Universum (22:15 MEZ, Alpaca Multi-Symbol-Batch)
3. `technical_indicators_computer` – TA-Indikatoren berechnen (22:30 MEZ) ✅ Sprint 6
4. `ark_holdings` – ARK-ETF-Holdings via arkfunds.io + Delta-Berechnung (23:00 MEZ)
5. `form4_collector` – Neue Form-4-Filings der letzten 24h + Cluster-Berechnung (23:30 MEZ)
6. `feature_pipeline` – Feature Snapshot für den Tag erzeugen (Sprint 8)
7. `target_backfill` – Returns für ältere Snapshots nachtragen (Sprint 8)

**Wöchentlich (Sonntag):**
- `fundamentals_collector` – Fundamental-Metriken via yfinance (01:00 MEZ) ✅ Sprint 5
- `analyst_ratings_collector` – Läuft auch sonntags (01:00 MEZ)
- `earnings_calendar_collector` – Earnings-Termine via yfinance (02:00 MEZ) ✅ Sprint 5
- `form13f_collector` – Neue 13F-Filings (falls Quartalsende gewesen) (10:00 MEZ)
- `politician_trades_collector` – Senate eFD PTR-Scraping (11:00 MEZ)

**Monatlich (1. des Monats):**
- `index_sync` – S&P 500 / Nasdaq 100 Mitgliedschaft aktualisieren + **Sektor-Enrichment** für neue Ticker (03:00 MEZ) ✅ Sprint 7+

**Manuell (via UI):**
- `price_backfill` – Historische Preise ab 2021-01-01 laden (Settings > Backfill)
- `indicator_backfill` – Alle TA-Indikatoren neu berechnen (Settings > Backfill)
- `sector_enrichment` – Fehlende Sektor-/Branchendaten von yfinance laden (Settings > Sektoren nachladen)
- `db_reset` – Factory Reset: Alle Datentabellen löschen (Settings > Werkszustand)
- `vacuum_analyze` – PostgreSQL VACUUM + ANALYZE (Settings > VACUUM)

---

## Wichtige technische Entscheidungen

Siehe [DECISIONS.md](DECISIONS.md) für Begründungen.

- **PostgreSQL statt SQLite**: Concurrent-Writes, JSONB-Support, spätere API-Zugriffe
- **Append-only Raw Layer**: Rückverfolgbarkeit, Schema-Änderungen ohne Datenverlust
- **APScheduler statt Cron**: Bessere Kontrolle aus Python, Logging, Fehlerbehandlung
- **SQLAlchemy 2.0 statt Raw SQL**: Type-Safety, Migrations, Testbarkeit
- **Alpaca als primäre Preisquelle** (Sprint 1b): Kurs-Konsistenz mit Trading-Plattform, stabil, offiziell
- **yfinance als Fallback**: Code bleibt erhalten, nicht mehr im Scheduler
- **arkfunds.io statt ARK CSV**: CSV gibt 403, JSON-API ist robuster
- **Wikipedia für Index-Listen**: Kostenlos, aktuell genug bei ~4 Rebalancings/Jahr
- **Senate eFD statt Quiver API**: Kostenlos, offizielle Primärquelle, kein API-Token nötig
- **yfinance für Fundamentals/Ratings/Earnings**: Kostenlos, alle Felder verfügbar, Risiko: inoffizielle API
- **Nachtslot 01:00–03:00 MEZ für yfinance**: Keine Kollision mit Abend-Jobs, Yahoo weniger belastet nachts
- **Separater Container statt geteilte DB**: Isolation von GynOrg, unabhängige Backups
- **pandas-ta statt ta-lib**: Reines Python, kein C-Compiler nötig im Docker, Performance für EOD ausreichend
- **Preis-Backfill ab 2021**: Preise sind Basisdaten (kein Signal); ML braucht ≥500k Samples; Signal-Backfill weiterhin NEIN
- **Relative Strength via Excess Return**: `ticker_ret_20d − spy_ret_20d` statt Ratio (kein Division-by-Zero)
- **FastAPI + Vite/React SPA statt Streamlit**: Volle Design-Kontrolle für Stitch „Precision Architect“ Design System
- **Einzelner Container**: Collector + API + UI in einem Container auf Port 8090, Multi-Stage Docker Build
- **Dashboard vor Feature Pipeline**: UI (Sprint 7) vor Backend-Aggregation (Sprint 8) für sofortiges grafisches Feedback
- **yfinance für Sektor-Enrichment**: Alpaca liefert kein `sector`/`industry`; yfinance `ticker.info` als kostenlose Quelle, in monatlichen IndexSync integriert
