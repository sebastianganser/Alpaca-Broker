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
│                                                             │
│  ┌───────────────────┐    ┌────────────────────────────┐    │
│  │ signal-collector  │    │ postgresql18-alpaca        │    │
│  │ (Python Container)│───▶│ (separater Container)      │    │
│  │                   │    │ DB: broker_data            │    │
│  │ APScheduler:      │    │ Schema: signals            │    │
│  │ • Täglich 22:00   │    │ User: sebastian            │    │
│  │   (nach US-EOD)   │    │ Port: 5435                 │    │
│  │                   │    │ Volume: /mnt/user/         │    │
│  └───────────────────┘    │   Datafolder/Broker/       │    │
│                           └────────────────────────────┘    │
│                                                             │
│  ┌───────────────────┐    ┌────────────────────────────┐    │
│  │ signal-api        │    │ Alpaca API                 │    │
│  │ (FastAPI, später) │───▶│ (Paper Trading NUR!)       │    │
│  │ Port: 8090        │    │ Endpoint hardcoded check   │    │
│  └───────────────────┘    └────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

**Netzwerk:** Alle Container laufen in einem eigenen Docker-Netzwerk `trading-signals-net`. 
**Backup:** Tägliches pg_dump via Cron auf Unraid, in den bestehenden Backup-Ordner.

---

## Projektstruktur (Zielbild)

```
trading-signals/
├── CLAUDE.md                      # Projekt-Einstiegspunkt
├── README.md                      # GitHub-Übersicht
├── docs/                          # Alle Dokumentation
│   ├── ARCHITECTURE.md            # Dieses Dokument
│   ├── ROADMAP.md
│   ├── DATA_SOURCES.md
│   ├── DECISIONS.md
│   └── LEARNINGS.md
├── infra/                         # Deployment
│   ├── docker-compose.yml
│   ├── docker-compose.override.yml.example  # Für lokale Entwicklung
│   ├── Dockerfile.collector
│   ├── Dockerfile.api             # später
│   └── .env.example
├── src/
│   ├── trading_signals/           # Python-Paket
│   │   ├── __init__.py
│   │   ├── config.py              # Pydantic Settings
│   │   ├── db/                    # Datenbank-Layer
│   │   │   ├── __init__.py
│   │   │   ├── base.py            # SQLAlchemy Base
│   │   │   ├── session.py         # Session-Factory
│   │   │   └── models/            # ORM-Modelle pro Tabelle
│   │   │       ├── prices.py
│   │   │       ├── ark.py
│   │   │       ├── insider.py
│   │   │       ├── politicians.py
│   │   │       ├── fundamentals.py
│   │   │       ├── universe.py
│   │   │       └── features.py
│   │   ├── collectors/            # Daten-Sammler (ein Modul pro Quelle)
│   │   │   ├── __init__.py
│   │   │   ├── base.py            # Abstract BaseCollector
│   │   │   ├── prices_yfinance.py
│   │   │   ├── ark_holdings.py
│   │   │   ├── sec_form4.py
│   │   │   ├── sec_form13f.py
│   │   │   ├── politicians.py
│   │   │   ├── fundamentals_yf.py
│   │   │   └── analyst_ratings.py
│   │   ├── derived/               # Berechnete Features
│   │   │   ├── __init__.py
│   │   │   ├── ark_deltas.py
│   │   │   ├── insider_clusters.py
│   │   │   ├── technical_indicators.py
│   │   │   └── feature_pipeline.py  # Aggregiert alles zu feature_snapshots
│   │   ├── universe/              # Dynamisches Titel-Universum
│   │   │   ├── __init__.py
│   │   │   └── manager.py
│   │   ├── scheduler/             # Job-Orchestrierung
│   │   │   ├── __init__.py
│   │   │   └── jobs.py
│   │   └── utils/
│   │       ├── logging.py
│   │       └── retry.py
│   └── alembic/                   # Datenbank-Migrationen
│       ├── env.py
│       └── versions/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── scripts/                       # Einmal-Skripte
│   ├── init_universe.py           # Startuniversum befüllen
│   └── db_health_check.py
├── pyproject.toml                 # Poetry oder uv
├── .gitignore
├── .env.example
└── .python-version
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
  added_by        VARCHAR(50),        -- 'ark_etf', 'form4', 'manual', ...
  is_active       BOOLEAN DEFAULT TRUE,
  last_seen       DATE,
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
  adj_close       NUMERIC(16,4),
  volume          BIGINT,
  source          VARCHAR(50),       -- 'yfinance', 'alpaca'
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
  etf_ticker      VARCHAR(10) NOT NULL,   -- ARKX, ARKK, ARKQ, ...
  ticker          VARCHAR(20) NOT NULL,
  company_name    VARCHAR(200),
  cusip           VARCHAR(20),
  shares          NUMERIC(20,4),
  market_value    NUMERIC(20,2),
  weight_pct      NUMERIC(8,4),
  source_url      TEXT,
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
  form4_url       TEXT,
  raw_data        JSONB,                 -- Komplette XML/JSON für Audit
  fetched_at      TIMESTAMP DEFAULT NOW()
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
US-Politiker-Trades (Capitol Trades o.ä.).

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
  transaction_type VARCHAR(20),          -- 'Buy', 'Sale'
  amount_range    VARCHAR(50),           -- '$1,001 - $15,000' etc.
  source_url      TEXT,
  raw_data        JSONB,
  fetched_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_politician_ticker ON signals.politician_trades(ticker);
```

#### `signals.fundamentals_snapshot`
Fundamentaldaten pro Titel, täglicher Snapshot.

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
Analyst-Kursziele und Rating-Changes.

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
  action          VARCHAR(50),           -- 'Upgrade', 'Downgrade', 'Initiate'
  raw_data        JSONB,
  fetched_at      TIMESTAMP DEFAULT NOW()
);
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
┌────────────────┐  ┌────────────────┐  ┌────────────────┐
│ ARK Funds CSV  │  │ SEC EDGAR API  │  │ yfinance       │
└────────┬───────┘  └────────┬───────┘  └────────┬───────┘
         │                   │                   │
         ▼                   ▼                   ▼
┌─────────────────────────────────────────────────────────┐
│                    Collectors (Python)                  │
│  [ark] [form4] [form13f] [prices] [fundamentals] [news] │
└────────────────────────┬────────────────────────────────┘
                         │ INSERT
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Raw Layer (append-only)                    │
│  prices_daily | ark_holdings | insider_trades | ...     │
└────────────────────────┬────────────────────────────────┘
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

**Täglich 22:00 Unraid-Zeit (nach US-Börsenschluss 22:00 MEZ = 16:00 ET):**

1. `prices_collector` – OHLCV für das gesamte Universum
2. `ark_collector` – Alle ARK-ETF-CSVs herunterladen und parsen
3. `form4_collector` – Neue Form-4-Filings der letzten 24h
4. `fundamentals_collector` – Fundamentaldaten aktualisieren (nicht jeden Tag nötig)
5. `analyst_collector` – Analyst-Ratings
6. `ark_deltas_computer` – Deltas berechnen
7. `technical_indicators_computer` – TA-Indikatoren berechnen
8. `insider_clusters_computer` – Cluster-Erkennung
9. `feature_pipeline` – Feature Snapshot für den Tag erzeugen
10. `target_backfill` – Returns für ältere Snapshots nachtragen

**Wöchentlich (Sonntag):**
- `form13f_collector` – Neue 13F-Filings (falls Quartalsende gewesen)
- `universe_cleanup` – Inaktive Titel markieren
- `db_maintenance` – VACUUM, ANALYZE

---

## Wichtige technische Entscheidungen

Siehe [DECISIONS.md](DECISIONS.md) für Begründungen.

- **PostgreSQL statt SQLite**: Concurrent-Writes, JSONB-Support, spätere API-Zugriffe
- **Append-only Raw Layer**: Rückverfolgbarkeit, Schema-Änderungen ohne Datenverlust
- **APScheduler statt Cron**: Bessere Kontrolle aus Python, Logging, Fehlerbehandlung
- **SQLAlchemy 2.0 statt Raw SQL**: Type-Safety, Migrations, Testbarkeit
- **yfinance als erste Preis-Quelle**: Kostenlos, ausreichend für EOD-Daten, leicht ersetzbar
- **Separater Container statt geteilte DB**: Isolation von GynOrg, unabhängige Backups
