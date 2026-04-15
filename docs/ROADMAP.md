# ROADMAP.md – Sprint-Planung & Fortschritt

> **Lebendes Dokument.** Wird nach jedem Sprint aktualisiert.
> Hier siehst du auf einen Blick, was erledigt ist, was gerade läuft und was als Nächstes kommt.

---

## Status-Übersicht

| Sprint | Titel | Status | Datum |
|---|---|---|---|
| 0 | Fundament (Docker, DB, Struktur) | 🟢 Erledigt | April 2026 |
| 1 | Price Collector (yfinance → Alpaca) | 🟢 Erledigt | April 2026 |
| 2 | ARK Holdings Tracker | 🟢 Erledigt | April 2026 |
| 3 | SEC EDGAR (Form 4 + 13F) | 🟢 Erledigt | April 2026 |
| 4 | Politiker-Trades (Senate eFD) | 🟢 Erledigt | April 2026 |
| 5 | Fundamentals + Analyst-Daten | 🟢 Erledigt | April 2026 |
| 6 | Technische Indikatoren | 🟢 Erledigt | April 2026 |
| 7 | Dashboard & Operations UI | 🟢 Erledigt | April 2026 |
| 8 | Feature Pipeline | 🔴 Offen | – |
| **⏸ Wartephase** | **2–3 Monate Datensammlung** | **–** | **–** |
| 9 | Erste explorative Analyse (Jupyter) | 🔴 Offen | – |
| 10 | Signal-Scoring-Modelle | 🔴 Offen | – |
| 11 | Backtest-Framework | 🔴 Offen | – |
| 12 | Paper-Trading-Integration | 🔴 Offen | – |

**Legende:** 🔴 Offen · 🟡 In Arbeit · 🟢 Erledigt · ⏸ Pausiert

---

## Erledigte Sprints

### Sprint 0 – Fundament

**Ziel:** Ein lauffähiges Grundgerüst auf dem Unraid-Server, in dem später alle Collectors ihre Arbeit verrichten können.

**Aufgaben:**
- [x] Projektordner auf Windows anlegen: `D:\Sebastian\Dokumente\Privat\Rudi\Coding\Workspaces\Alpaca-Broker`
- [x] Git-Repo initialisieren, `.gitignore` einrichten
- [x] GitHub-Repo erstellen (privat): `sebastianganser/Alpaca-Broker`
- [x] Projektdokumentation in `docs/` ablegen (dieses Dokument inklusive)
- [x] `docker-compose.yml` – Collector-Service (DB läuft als eigener Container `postgresql18-alpaca`)
- [x] `.env.example` mit allen nötigen Variablen
- [x] `Dockerfile.collector` mit Python 3.12, uv Setup
- [x] `pyproject.toml` mit Dependencies (uv als Paketmanager)
- [x] SQLAlchemy 2.0 Base-Klasse, Session-Factory
- [x] Alembic initialisieren
- [x] Erste Migration: Schema `signals` anlegen
- [x] Zweite Migration: Tabelle `universe` anlegen
- [x] Skript `scripts/init_universe.py` – befüllt Startuniversum (103 Ticker: S&P 100 + SPY)
- [x] 11 Unit-Tests (Config + Universe-Modell)
- [ ] Deployment auf Unraid testen *(blockiert: erst nach Sprint 8 – siehe [DECISIONS.md](DECISIONS.md))*
- [ ] Grundlegender Health-Check-Endpoint (Sprint 8)

**Definition of Done:**
- Der `trading-signals-collector`-Container startet auf Unraid ohne Fehler
- Die `trading-signals-db`-Instanz ist erreichbar und enthält das Schema `signals`
- Das `universe`-Skript kann manuell ausgeführt werden und legt die Start-Titel an
- Alles ist in Git committed und auf GitHub

**Modell-Empfehlung für Sprint 0:** Sonnet 4.6 (Standard-Coding, keine komplexe Logik)

---

## Nächste Sprints im Detail

### Sprint 1 – Price Collector (yfinance) ✅

**Ziel:** Täglicher OHLCV-Download für das gesamte Universum.

**Aufgaben:**
- [x] `BaseCollector`-Klasse definieren (Abstract, Template-Method-Pattern)
- [x] `PriceCollectorYFinance` implementieren
- [x] Batch-Fetching (50er-Batches, nicht pro Ticker einzeln)
- [x] Rate-Limiting und Retry-Logik (`@retry` Decorator mit Exponential Backoff)
- [x] Idempotentes Insert-Pattern für `prices_daily` (`ON CONFLICT DO NOTHING`)
- [x] Logging in `collection_log` (inkl. Gap-Statistiken)
- [x] Gap Detection & Repair (NYSE-Kalender, Forward-Fill-Extrapolation)
- [x] Unit-Tests mit Mock-Daten (29 neue Tests, 40 gesamt)
- [x] APScheduler-Job für tägliche Ausführung (22:15 MEZ)
- [x] Ticker-Mapping für Sonderfälle (BRK.B → BRK-B)

**Definition of Done:** ✅ 1.020 OHLCV-Datenpunkte für 102/103 Ticker (10 Handelstage) in der DB. WBA (Walgreens) ist bei Yahoo delisted.

---

### Sprint 2 – ARK Holdings Tracker ✅

**Ziel:** Tägliche Snapshots aller aktiven ARK-ETFs.

**Aufgaben:**
- [x] Datenquelle recherchiert: arkfunds.io JSON-API statt CSV-Scraping (ark-funds.com gibt 403)
- [x] `ARKHoldingsCollector` implementieren (8 ETFs, arkfunds.io API)
- [x] Cash-/Treasury-Positionen filtern (Regex)
- [x] Automatische Universum-Erweiterung mit Alpaca-Validierung
- [x] `ARKDeltaComputer` für Derived Layer (new/closed/increased/decreased/unchanged)
- [x] Tests mit Fixtures + Mocks (25 neue Tests, 71 gesamt)
- [x] Error-Handling: Retry-Logik, graceful bei API-Fehlern
- [x] APScheduler-Job (23:00 MEZ)

**Definition of Done:** ✅ 322 Holdings-Positionen für alle 8 ARK-ETFs (Snapshot 10.04.2026) in der DB. 150 neue Ticker via Alpaca validiert ins Universe aufgenommen (102 → 252 aktive Ticker). Delta-Berechnung bereit (läuft ab 2. Snapshot).

---

### Sprint 3 – SEC EDGAR ✅

**Ziel:** Form 4 Insider-Trades und Form 13F institutionelle Holdings.

**Aufgaben:**
- [x] SEC EDGAR API-Zugang dokumentieren (User-Agent-Header beachten!)
- [x] `SECClient` – zentraler API-Client mit Rate Limiting (10 req/s) und CIK-Mapping
- [x] `Form4Collector` – Universe-driven: parst XML-Filings für alle 644 Ticker
- [x] `Form13FCollector` – Filer-driven: Top-20 institutionelle Investoren (Buffett, Burry, etc.)
- [x] Dedup-Logik (Unique Constraints auf beiden Tabellen)
- [x] `InsiderClusterComputer` – erkennt Cluster-Käufe (≥2 Insider in 21 Tagen)
- [x] CIK ↔ Ticker Mapping (via SEC `company_tickers.json`)
- [x] ORM-Modelle (`InsiderTrade`, `InsiderCluster`, `Form13FHolding`)
- [x] Alembic Migrationen (006 + 007)
- [x] 67 neue Tests (154 gesamt, alle grün)
- [x] APScheduler-Jobs: Form 4 täglich 23:30, 13F wöchentlich Sonntag 10:00

**Definition of Done:** ✅ Form 4 Collector, 13F Collector, InsiderClusterComputer, SECClient implementiert. 154 Tests grün.

---

### Sprint 4 – Politiker-Trades

**Ziel:** Als zusätzliche Signalquelle, trotz bekannter Verzögerungen.

**Aufgaben:**
- [x] Quelle wählen: Senate eFD (kostenlos, offizielle Regierungsquelle)
- [x] Rechtliche Aspekte geprüft: Offizielle öffentliche Daten, kein ToS-Problem
- [x] `DisclosureClient` für Senate eFD Scraping implementiert
- [x] `PoliticianTradesCollector` implementiert (BaseCollector-Pattern)
- [x] ORM-Modell `PoliticianTrade` + Alembic Migration 008
- [x] Scheduler-Job: Wöchentlich Sonntag 11:00 MEZ
- [x] 46 neue Tests (200 gesamt, alle grün)
- [ ] House Clerk PTRs (PDF-only, future enhancement)

**Definition of Done:** Politiker-Trades werden regelmäßig erfasst und sind in der DB abrufbar.

---

### Sprint 5 – Fundamentals + Analyst-Daten ✅

**Ziel:** P/E, P/S, Revenue-Growth, Analyst-Ratings, Earnings-Kalender.

**Aufgaben:**
- [x] `YFinanceClient` – Shared Client mit Rate-Limiting (0.5s/Ticker, 3s/Batch), Batch-Iteration, Graceful Error Handling
- [x] `FundamentalsCollectorYF` – 18 Metriken aus `ticker.info` + `eps_growth_yoy` aus `get_earnings_estimate()`
- [x] `AnalystRatingsCollector` – Upgrades/Downgrades aus `ticker.upgrades_downgrades`, 30-Tage-Lookback
- [x] `EarningsCalendarCollector` – Earnings-Termine mit EPS-Estimates und Surprises
- [x] ORM-Modelle: `FundamentalsSnapshot`, `AnalystRating`, `EarningsCalendar`
- [x] Alembic Migrationen (009 + 010 + 011)
- [x] 68 neue Tests (268 gesamt, alle grün)
- [x] APScheduler: Fundamentals So 01:00, Analyst-Ratings tägl. 01:00, Earnings So 02:00 (Nachtslot)
- [x] UPSERT für Fundamentals/Earnings (ändern sich), DO NOTHING für Ratings (Dedup)

**Definition of Done:** ✅ Für jeden Universum-Titel gibt es aktuelle Fundamentals, Ratings und Earnings-Termine. 268 Tests grün.

---

### Sprint 6 – Technische Indikatoren ✅

**Ziel:** Berechnung aller gängigen TA-Indikatoren aus den Preisdaten.

**Aufgaben:**
- [x] Historischer Price-Backfill via Alpaca ab 01.01.2021 (~882k Rows, ~5,3 Jahre Tiefe)
- [x] `TechnicalIndicatorsComputer` mit `pandas-ta` (14 Indikatoren)
- [x] SMA 20/50/200, EMA 12/26, RSI 14, MACD (Line/Signal/Histogram), Bollinger Bands, ATR 14
- [x] Volume SMA 20, Relative Strength vs. SPY (Excess Return / Return-Differenz)
- [x] ORM-Modell `TechnicalIndicator` + Alembic Migration 012
- [x] Täglicher Job nach Price-Collector (22:30 MEZ, CronTrigger)
- [x] Min-Data-Checks: Indikatoren nur berechnet wenn genug Historie vorhanden
- [x] SPY-Cache: SPY-Preise einmal laden, für alle Ticker wiederverwenden
- [x] UPSERT-Pattern (ON CONFLICT DO UPDATE) für Idempotenz
- [x] Backfill-Modus: Alle historischen Tage auf einmal berechnen
- [x] 35 neue Tests (303 gesamt, alle grün)
- [x] `pandas-ta>=0.3.14` zu Dependencies hinzugefügt

**Definition of Done:** ✅ Für jeden Titel und jeden Handelstag sind die TA-Indikatoren berechnet und in `technical_indicators` gespeichert. 303 Tests grün.

---

### Sprint 7 – Dashboard & Operations UI ✅

**Ziel:** Browserbasiertes Frontend für grafisches Feedback und Betriebssteuerung.

> Deployment-Gate: Deployment auf Unraid nun freigegeben.

**Technologie:** FastAPI (Backend-API) + Vite/React SPA (Frontend) im gleichen Container
**Design:** Stitch "Precision Architect" - Dark Mode, Cyan Primary (#28EBCF), Inter Font

**Aufgaben:**
- [x] **FastAPI-Backend** mit API-Endpoints (5 Router, 20+ Endpoints)
- [x] **Vite/React SPA** mit dem Stitch Design System
- [x] **Dashboard (Startseite):**
  - [x] Collector-Status (Übersicht aller Jobs: letzter Lauf, Status, nächster Run)
  - [x] Datenbestand pro Tabelle (Row-Counts, Zeiträume)
  - [x] System-Health (DB-Verbindung, Alembic-Stand, Uptime, Job-Count)
- [x] **Universe-Übersicht:**
  - [x] Alle Ticker mit Status, Index-Zugehörigkeit, letztem Preis
  - [x] Filter (Index, Sektor, Aktivstatus) + Suchfeld + Pagination
- [x] **Signals Explorer:**
  - [x] ARK-Deltas, Insider-Cluster, Politiker-Trades, Analyst-Ratings (Tabs)
- [x] **Einstellungen / Operations:**
  - [x] Backfill starten (Price-Backfill + TA-Backfill) mit Fortschrittsanzeige
  - [x] DB-Bereinigung (VACUUM/ANALYZE) + Stats
  - [x] Alembic-Status
  - [x] Scheduler-Übersicht (Jobs, nächste Ausführung, manueller Trigger)
- [x] **Ticker-Detail** (Preischart mit SMA/Bollinger, Indikatoren, Fundamentals, Signale)
- [x] Docker Multi-Stage Build (Node + Python + Runtime)

**Definition of Done:** Sebastian kann im Browser alle Daten sehen, Collectors überwachen, Backfills starten und DB-Wartung durchführen. ✅

---

### Sprint 8 – Feature Pipeline ⭐

**Ziel:** Das Herzstück – aggregiere alle Rohdaten und Derived-Daten in `feature_snapshots`.

**Aufgaben:**
- [ ] `FeaturePipeline`-Klasse
- [ ] Aggregations-Logik für jede Feature-Gruppe
- [ ] ARK-Conviction-Score-Berechnung
- [ ] Insider-Cluster-Score-Berechnung
- [ ] Analyst-Consensus-Score
- [ ] Target-Variablen-Nachtragung (1d, 5d, 20d, 60d Returns)
- [ ] Täglicher Job nach allen Collectors und Derived-Computern
- [ ] Dashboard-Integration: Feature-Snapshot-Views im UI ergänzen
- [ ] Tests mit End-to-End-Szenarien

**Definition of Done:** Für jeden Titel im Universum gibt es für den aktuellen Tag einen vollständigen Feature-Vektor. Zielvariablen werden nach 1/5/20/60 Tagen automatisch nachgetragen. Dashboard zeigt Feature-Daten an.

---

## 🚢 Deployment auf Unraid

**Nach Sprint 7:** Docker-Image bauen, `docker-compose up`, Alembic läuft automatisch via `entrypoint.sh`, Backfill über UI starten.
**Voraussetzungen:**
- [x] Alle Daten-Collectors implementiert (Sprint 1–6 ✅)
- [x] Dashboard/UI mit Operations-Tools (Sprint 7 ✅)
- [x] Erster erfolgreicher Docker-Deployment auf Unraid ✅

## ⚓ Deployment auf Unraid – Abgeschlossen ✅

**Status:** System läuft produktiv auf `192.168.1.93:8090`.

**Infrastruktur:**
- Clone-and-Build auf Unraid: `/mnt/user/appdata/alpaca-broker`
- Docker Compose via Compose Manager: `/boot/config/plugins/compose.manager/projects/Alpaca-Broker/`
- PostgreSQL 18 extern (`postgresql18-alpaca`, Port 5435)
- Alembic-Migrationen laufen automatisch via `entrypoint.sh`
- 10 Scheduler-Jobs aktiv (5 täglich, 4 wöchentlich, 1 monatlich)

**Update-Workflow:**
1. `git push` von Windows
2. Unraid Terminal: `cd /mnt/user/appdata/alpaca-broker && git pull && docker compose -f /boot/config/plugins/compose.manager/projects/Alpaca-Broker/docker-compose.yml up --build -d`

**Post-Deployment Features (Sprint 7+):**
- [x] Fix: Dashboard Health Check (public.alembic_version)
- [x] Backfill Progress Tracking: Echtzeit-Fortschritt für Price + TA Backfills (Ticker, %, ETA)
- [x] Factory Reset: DB-Werkszustand über UI (löscht alle Daten, behält Universe)
- [x] Monthly Index Sync: Automatischer S&P 500 / Nasdaq 100 Abgleich (1. des Monats, 03:00)
- [x] Datenqualitäts-Kachel: Per-Ticker Data Quality Assessment auf der TickerPage (Preise, TA, Fundamentals, Scheduler)

## ⏸ Wartephase: 2–3 Monate Datensammlung

**Kein aktiver Sprint, aber wichtige Aktivitäten:**
- Regelmäßig prüfen, ob alle Collectors stabil laufen
- Gelegentlich mit Claude Desktop ad-hoc die Daten explorieren
- Erste Beobachtungen in `LEARNINGS.md` festhalten
- Falls Datenlücken auffallen: Collectors verbessern
- Parallel ein Benchmark-Portfolio (nur S&P 500) im Paper-Trading-Konto führen

---

## Nach der Wartephase

### Sprint 9 – Erste explorative Analyse

**Ziel:** Verstehen, was in den Daten steckt.

**Aufgaben:**
- [ ] Jupyter-Notebook-Setup
- [ ] Deskriptive Statistiken über alle Features
- [ ] Korrelationsmatrix Features ↔ zukünftige Returns
- [ ] Feature-Importance mit Random Forest
- [ ] Erste Erkenntnisse in `LEARNINGS.md` dokumentieren
- [ ] Modell-Empfehlung: **Opus 4.6** für Interpretation der Ergebnisse

**Definition of Done:** Wir wissen, welche Features potenziell Alpha liefern könnten und welche reines Rauschen sind.

---

### Sprint 10 – Signal-Scoring

**Ziel:** Einen robusten Scoring-Mechanismus, der die besten Features kombiniert.

**Aufgaben:**
- [ ] Gewichtetes Scoring-Modell implementieren
- [ ] Optional: LASSO-Regression für Feature-Auswahl
- [ ] Optional: Gradient Boosting für nicht-lineare Kombinationen
- [ ] Score-Persistierung in der DB
- [ ] Täglicher Job für Score-Berechnung

**Definition of Done:** Für jeden Titel gibt es einen täglichen Score zwischen -1 und +1.

---

### Sprint 11 – Backtest-Framework

**Ziel:** Strategien rückwirkend auf den gesammelten Daten testen.

**Aufgaben:**
- [ ] Walk-Forward-Testing-Engine
- [ ] Transaktionskosten-Modell
- [ ] Performance-Metriken (Sharpe, Max Drawdown, Hit Rate)
- [ ] Vergleich gegen Benchmark
- [ ] Reporting der Backtest-Ergebnisse

**Definition of Done:** Wir können beliebige Regel-Sets auf historischen Daten testen und sehen Performance-Metriken.

---

### Sprint 12 – Paper-Trading-Integration

**Ziel:** Die beste Strategie aus dem Backtest läuft live im Paper-Konto.

**Aufgaben:**
- [ ] Alpaca-Broker-Adapter mit Paper-Guard
- [ ] Order-Management (Stop-Loss, Take-Profit)
- [ ] Position-Sizing nach Risk-Rules
- [ ] Tägliche Signal-zu-Order-Übersetzung
- [ ] Manuelle Freigabe vor jedem Trade (Sicherheitsmechanismus)
- [ ] Performance-Tracking gegen Backtest-Erwartung

**Definition of Done:** Das System generiert täglich Signale, die im Alpaca-Paper-Konto gehandelt werden. Performance wird getrackt und mit Backtest-Erwartung verglichen.

---

## Langfristige Ideen (Backlog)

Ideen, die später interessant werden könnten, aber aktuell nicht priorisiert sind:

- **Crypto-Integration:** On-Chain-Whale-Tracking für Ethereum, Arkham-API-Anbindung
- **News-Sentiment:** NLP-Analyse von Headlines mit Haiku
- **Reddit/Social-Sentiment:** StockTwits, WSB-Mentions als Contrarian-Signal
- **Optionen-Flow:** Unusual Options Activity als zusätzliches Signal
- **ML-Modelle:** XGBoost, Neural Networks nach mindestens 12 Monaten Datenhistorie
- **Mehrere ETFs tracken:** Nicht nur ARK, sondern auch andere "Smart Money"-Fonds
- **Europäische Aktien:** Deutsche / europäische Small Caps mit eigenen Signalquellen
- **Live-Trading:** Erst nach mindestens 12 Monaten erfolgreichem Paper-Trading überdenken

---

## Session-Log

> Chronologische Notizen, was in welcher Session passiert ist. Hilft beim Wiedereinstieg.

### Session 1 – April 2026 – Konzept und Dokumentation
- Grundkonzept diskutiert, Video-Vorlage kritisch evaluiert
- Entscheidung: Kein Trading zu Beginn, stattdessen Signal Warehouse aufbauen
- Datenquellen-Strategie festgelegt (maximal sammeln, später filtern)
- Architektur-Draft erstellt
- Sprint-Planung (12 Sprints + Wartephase)
- Alle Dokumente angelegt (`CLAUDE.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `DATA_SOURCES.md`, `DECISIONS.md`, `LEARNINGS.md`)
- Nächster Schritt: **Sprint 0 starten**

### Session 2 – 12. April 2026 – Sprint 0 Implementierung
- Lokale Umgebung verifiziert: Python 3.12.6, uv 0.11.2, Git 2.47.1, Docker 27.4.0
- GitHub-Repo erstellt: `sebastianganser/Alpaca-Broker` (privat)
- PostgreSQL 18.3 auf Unraid bestätigt: `postgresql18-alpaca` (192.168.1.93:5435, DB: broker_data)
- Komplette Projektstruktur angelegt (src-Layout mit uv)
- Pydantic Settings mit Alpaca-Safety-Check implementiert
- SQLAlchemy 2.0 Base + Session-Factory + Universe-Modell
- Alembic konfiguriert: Schema `signals` + Tabelle `universe` migriert
- 103 Ticker (S&P 100 + SPY) ins Universum geladen
- 11 Unit-Tests (alle grün)
- Docker Compose + Dockerfile für Collector erstellt
- ARCHITECTURE.md mit echten DB-Details aktualisiert
- Nächster Schritt: **Git Push, dann Sprint 1 (Price Collector)**

### Session 3 – 12. April 2026 – Sprint 1 Implementierung
- Dokumentations-Audit: 20 Inkonsistenzen in CLAUDE.md, README.md, ARCHITECTURE.md, DATA_SOURCES.md gefunden und behoben
- Dependencies: yfinance, pandas, pandas-market-calendars, scipy hinzugefügt
- Migration 003: `prices_daily` (mit `is_extrapolated`-Flag + Partial Index) + `collection_log` (mit Gap-Statistiken)
- `@retry`-Decorator mit exponentiellem Backoff (nur transiente Fehler)
- **GapDetector**: NYSE-Kalender-basierte Lückenerkennung → Nachladen → Forward-Fill-Extrapolation
- **BaseCollector**: Template-Method-Pattern mit integriertem Gap-Check, Session-Expunge für detached Log-Objekte
- **PriceCollectorYFinance**: Batch-Download (50er-Batches), Ticker-Mapping (BRK.B→BRK-B), ON CONFLICT DO NOTHING
- Erster Live-Lauf: 1.020 Datenpunkte für 102 Ticker (10 Handelstage) erfolgreich geladen
- WBA (Walgreens) bei Yahoo delisted → kein Datenabruf möglich
- 29 neue Unit-Tests (40 gesamt, alle grün)
- APScheduler Entrypoint: `main.py` mit BlockingScheduler, CronTrigger 22:15, Graceful Shutdown
- Dockerfile CMD auf Scheduler-Entrypoint aktualisiert
- Grundsatzentscheidung: Organisches Datenwachstum für Signale (kein Signal-Backfill). *Preis-Backfill ab 2021 erfolgte in Sprint 6 – siehe DECISIONS.md*
- Alpaca-Universum-Validierung: WBA bei Alpaca nicht gefunden → deaktiviert (102 aktive Ticker)
- **Sprint 2 (ARK Holdings)**: arkfunds.io API statt CSV (403), 322 Holdings für 8 ETFs geladen
- 150 neue Ticker aus ARK-ETFs via Alpaca validiert ins Universe (102 → 252)
- ARKDeltaComputer implementiert (new/closed/increased/decreased/unchanged)
- 71 Tests gesamt (alle grün)
- **Sprint 1b (Alpaca Prices + Universe)**: yfinance durch Alpaca Market Data API ersetzt
- Universe erweitert: S&P 500 (503) + Nasdaq 100 (101) → 644 aktive Ticker
- `index_membership` ARRAY-Spalte in universe (sp500, nasdaq100)
- IndexSyncer: Wikipedia-basierter Index-Sync mit Alpaca-Validierung
- PriceCollectorAlpaca: Multi-Symbol-Batch (100/Request), adjustment=all, IEX feed
- Erster Alpaca-Lauf: 2.700 neue Records fur 540 Ticker (<20s)
- 87 Tests gesamt (alle grün)

### Session 4 – 13. April 2026 – Sprint 3 Implementierung
- **SECClient**: Zentraler API-Client mit Rate Limiting (10 req/s), CIK↔Ticker Mapping via `company_tickers.json`
- **Form4Collector**: Universe-driven Ansatz (644 Ticker), SEC Submissions API + XML-Parsing
- **Form13FCollector**: Filer-driven (Top-20 institutionelle Investoren), infotable XML-Parsing
- **InsiderClusterComputer**: Cluster-Erkennung (≥2 Insider kaufen in 21-Tage-Fenster), Score-Berechnung
- ORM-Modelle: `InsiderTrade`, `InsiderCluster`, `Form13FHolding`
- Alembic Migrationen 006 (insider_trades + clusters) + 007 (form13f_holdings)
- Unique Constraints für Dedup auf beiden Tabellen
- APScheduler: Form 4 täglich 23:30 MEZ, 13F wöchentlich Sonntag 10:00 MEZ
- 67 neue Tests (154 gesamt, alle grün)
- Nächster Schritt: **Sprint 4 (Politiker-Trades)**

### Session 5 – 13. April 2026 – Sprint 4 Implementierung
- **Quiver Quantitative API verworfen** (30 $/Monat, Constraint: kostenlos bleiben)
- **DisclosureClient**: Scraper für Senate eFD (efdsearch.senate.gov) mit CSRF-Handling, Terms-Agreement, HTML-Parsing
- **PoliticianTradesCollector**: Senate-PTR-Abruf, Stock-Filterung, Ticker-Normalisierung, ON CONFLICT DO NOTHING
- ORM-Modell `PoliticianTrade` mit owner/asset_description/comment Feldern
- Alembic Migration 008: `politician_trades` Tabelle
- Dependencies: `requests` + `beautifulsoup4` hinzugefügt
- APScheduler: Wöchentlich Sonntag 11:00 MEZ
- 46 neue Tests (200 gesamt, alle grün)
- House PTRs bewusst ausgeklammert (PDF-only, zu komplex für Sprint 4)

### Session 6 – 13. April 2026 – Sprint 5 Implementierung
- **YFinanceClient**: Shared Client mit Rate-Limiting (0.5s/Ticker, 3s/Batch), Batch-Iteration, Graceful Error Handling
- **FundamentalsCollectorYF**: 18 Fundamental-Metriken aus `ticker.info` + `eps_growth_yoy` aus `get_earnings_estimate()`
- **AnalystRatingsCollector**: Upgrades/Downgrades aus `upgrades_downgrades` DataFrame, 30-Tage-Lookback, ON CONFLICT DO NOTHING
- **EarningsCalendarCollector**: Earnings-Termine mit EPS-Estimates und Surprises, UPSERT (eps_actual kommt nachträglich)
- ORM-Modelle: `FundamentalsSnapshot`, `AnalystRating`, `EarningsCalendar`
- Alembic Migrationen 009 (fundamentals_snapshot) + 010 (analyst_ratings) + 011 (earnings_calendar)
- Nachtslot 01:00–03:00 MEZ für alle yfinance-Jobs (keine Kollision mit bestehenden Daily-Jobs)
- 68 neue Tests (268 gesamt, alle grün)
- Nächster Schritt: **Sprint 6 (Technische Indikatoren)**

### Session 7 – 13. April 2026 – Sprint 6 Implementierung
- **Historischer Price-Backfill**: `scripts/backfill_prices.py` für Alpaca-Preisdaten ab 01.01.2021 (~882k Rows, ~5,3 Jahre Tiefe)
- **TechnicalIndicatorsComputer**: 14 Indikatoren via `pandas-ta` (SMA 20/50/200, EMA 12/26, RSI 14, MACD Line/Signal/Histogram, Bollinger Upper/Lower, ATR 14, Volume SMA 20, Relative Strength vs. SPY)
- **Relative Strength**: Excess Return (Return-Differenz) statt Ratio – kein Division-by-Zero, Quant-Standard
- ORM-Modell `TechnicalIndicator` + Alembic Migration 012
- APScheduler: Täglich 22:30 MEZ (15 Min nach Price Collector)
- Min-Data-Checks, SPY-Cache, UPSERT-Pattern, Backfill-Modus
- `pandas-ta>=0.3.14` als neue Dependency
- 35 neue Tests (303 gesamt, alle grün)
- Dokumentation aktualisiert: ROADMAP, ARCHITECTURE, CLAUDE.md, DECISIONS.md, README
- 3 neue Entscheidungen dokumentiert (Preis-Backfill, RS-Formel, Scheduling)
- Nächster Schritt: **Sprint 7 (Dashboard & Operations UI)**

### Session 8 – 13. April 2026 – Sprint 7 Implementierung
- **FastAPI-Backend**: `main.py` umgebaut von `BlockingScheduler` auf `BackgroundScheduler` + FastAPI Lifespan
- **5 API-Router**: Dashboard, Universe, Signals, Ticker, Operations (20+ Endpoints)
- **19 Pydantic Schemas** in `api/schemas.py`
- **BackfillManager**: Thread-basierte Async-Backfills mit Progress-Tracking (`api/tasks.py`)
- **Vite/React SPA**: 5 Pages (Dashboard, Universe, Signals, Settings, Ticker Detail)
- **Design System**: Stitch "Precision Architect" – Dark Mode, Cyan Primary, Inter Font, Tonal Depth
- **Ticker Detail**: Interaktiver Preischart (recharts) mit SMA/Bollinger Overlays, Indicator-Cards, Fundamentals
- **Docker**: 3-Stage Multi-Stage Build (Node + Python + Runtime), `entrypoint.sh` mit DB-Wait + Alembic
- **Dependencies**: react-router-dom, @tanstack/react-query, recharts, lucide-react
- TypeScript + Ruff Lint: 0 Errors, Vite Build erfolgreich (650KB JS, 9KB CSS)
- 303 Tests (alle grün, keine Regression)
- Nächster Schritt: **Deployment auf Unraid**, dann **Sprint 8 (Feature Pipeline)**

### Session 9 – 13. April 2026 – Unraid Deployment + Operational Fixes
- **Deployment auf Unraid**: Container läuft auf `192.168.1.93:8090`
- **Compose Manager**: `docker-compose.yml` + `.env` auf Unraid eingerichtet
- **Fix: README.md im Docker Build**: `Dockerfile.collector` fehlte `COPY README.md` für pyproject.toml
- **Fix: Dashboard Health Check**: Alembic-Query geändert auf `public.alembic_version`
- **Daten-Erstbefüllung**: ~820k Preisdatensätze + ~818k TA-Indikatoren via Backfill geladen
- **Backfill Progress Tracking**: `BackfillManager` komplett refactored
  - Price Backfill: Per-Batch Fortschritt (7 Batches à 100 Ticker), ETA-Schätzung
  - TA Backfill: Per-Ticker Fortschritt (644 Ticker), ETA-Schätzung
  - Frontend: Echtzeit-Anzeige (Ticker, %, ETA) mit 2s Polling
- **Factory Reset**: `POST /ops/db/reset` Endpoint + UI-Button mit Bestätigungsdialog
  - Löscht alle Datentabellen, behält Universe (644 Ticker) und Schema
- **Monthly Index Sync**: Neuer Scheduler-Job (1. des Monats, 03:00 MEZ)
  - Aktualisiert S&P 500 / Nasdaq 100 Mitgliedschaft von Wikipedia
  - Validiert neue Ticker gegen Alpaca, fügt sie automatisch zum Universe hinzu
- 303 Tests (alle grün)
- **SPA-Routing Fix**: Catch-All Fallback in FastAPI für Strg+F5 auf allen Seiten
- **Live Job-Status**: `JobTracker` mit APScheduler Event Listener
  - Settings: „⟳ Läuft..." Badge + deaktivierter Trigger-Button
  - Dashboard: Collector-Cards zeigen Echtzeit-Running-Status
  - Polling: Dashboard 5s, Settings 3s
- Dokumentation aktualisiert: CLAUDE.md, ROADMAP.md, ARCHITECTURE.md, DECISIONS.md, README.md
- Nächster Schritt: **Sprint 8 (Feature Pipeline)**

### Session 10 – 13. April 2026 – Datenqualitäts-Kachel
- **Datenqualitäts-Kachel** auf der TickerPage: Zeigt pro Ticker den Vollständigkeitsstatus
  - 4 Dimensionen: Preise (Tage + letztes Update), TA-Indikatoren (bis wann berechnet), Fundamentals (letzter Snapshot), Signal-Updates (Scheduler-Status + nächster Lauf)
  - Neuer Backend-Endpoint: `GET /api/v1/ticker/{symbol}/data-quality`
  - 2 neue Pydantic-Schemas: `DataQualityDimension`, `TickerDataQuality`
  - Frontend: `DataQualityCard` mit farbcodierten Werten (cyan/gelb/rot statt Icons)
  - Design-Fix: Redesign nach Stitch "Precision Architect" Regeln (grid-2, No-Line-Rule, keine Icons)
- Dokumentation aktualisiert: ARCHITECTURE.md, ROADMAP.md, DECISIONS.md

### Session 11 – 13. April 2026 – Sektor-Enrichment
- **Problem:** ~740 von 845 Tickern hatten keinen Sektor (Alpaca liefert kein `sector`)
- **Lösung:** Sektor/Branche-Enrichment via yfinance (`ticker.info` → `sector` + `industry`)
- **Backend:**
  - `YFinanceClient.fetch_sector_info()`: Neue leichtgewichtige Methode
  - `BackfillManager.start_sector_enrichment()`: Background-Thread wie Price/TA Backfill
  - `POST /api/v1/ops/backfill/sectors`: Neuer API-Endpoint
  - **Automatisierung:** `run_index_sync()` Job führt nach dem IndexSync automatisch Sektor-Enrichment für alle Ticker ohne Sektor aus
- **Frontend:**
  - Settings-Page: "Sektoren nachladen" Card (3. Backfill-Karte neben Prices/TA)
  - Progress-Bar + Ticker-Anzeige während Enrichment
- **CLI:** `scripts/enrich_universe_sectors.py` (standalone, mit --dry-run)
- **UI-Polish:**
  - Rebranding: Sidebar-Header "ALPACA BROKER" (Haupttitel) + "Signal Warehouse" (Untertitel)
  - Sidebar-Logo: `logo.png` neben Titel (flex-row), Nav-Icons zentriert unter Logo, Texte in Flucht
  - Browser-Tab: "Alpaca Broker"
  - Weltuhr im Sidebar: Frankfurt (Xetra), New York (NYSE), London (LSE), Tokyo (TSE)
  - Börsen-Status: Lucide Lock/LockOpen Icons zeigen Öffnungsstatus in Echtzeit
  - Handelszeiten: Xetra 9–17:30, NYSE 9:30–16, LSE 8–16:30, TSE 9–15
  - Logs-Seite: Scheduler-Logs mit Filterung (alle/Fehler)
- **Bugfix:** Alembic-Revision im Dashboard zeigte "—" → Query nutzte falsches Schema (`public` statt `signals`)
- Dokumentation aktualisiert: ARCHITECTURE.md, ROADMAP.md, DATA_SOURCES.md, DECISIONS.md
- Nächster Schritt: **Sprint 8 (Feature Pipeline)**

### Session 12 – 14./15. April 2026 – SEC Form 4 & Senate eFD Bugfix
- **Problem 1 – SEC Form 4 (404-Fehler):** Form 4 Collector konnte keine XML-Dateien herunterladen (alle 404)
  - **XSLT-Prefix-Bug:** `primaryDocument` enthielt XSLT-Wrapper-Pfade (`xslF345X06/ownership.xml`) → physische Dateien existieren ohne Prefix
  - **CIK-Routing-Bug:** URL-Konstruktion verwendete den Filer-CIK (aus Accession Number) statt Subject-Company-CIK → Dateien liegen unter Unternehmens-Verzeichnis
  - **Fix:** XSLT-Prefix strippen + Company-CIK aus `company_tickers.json` verwenden
  - **Ergebnis:** ✅ 1.329 Insider-Transaktionen erfolgreich importiert
- **Problem 2 – Senate eFD (403 → 503 → 0 Ergebnisse → ✅ 636 Trades):**
  - **TLS-Fingerprinting (403):** Senate eFD blockiert Python `requests` über JA3-Hash-Detection
  - **Fix:** Migration auf `curl_cffi` mit `impersonate="chrome131"` → 403 gelöst ✅
  - **HTML-Parsing (0 Ergebnisse):** Suchergebnisse werden per DataTables AJAX geladen, HTML-Tabelle ist leer
  - **Fix:** Direkt den AJAX-Endpoint `POST /search/report/data/` aufrufen → JSON statt HTML
  - **Session-Flow (503):** AJAX-Endpoint braucht vorherigen Search-Form POST + Senate.gov war temporär im Wartungsmodus
  - **Fix:** Search-Form POST + vollständige DataTables-Parameter (Column-Definitionen, CSRF via Header, Zeitstempel-Format)
  - **Ergebnis:** ✅ 161 PTR-Filings, 705 Einzeltransaktionen gelesen, **636 Politiker-Trades geschrieben** (1m 50s)
  - **Sichtbar im UI:** Boozman, Fetterman, Capito, Whitehouse, Tina Smith, Angus King u.a.
- **Neue Dependency:** `curl_cffi>=0.7` (in pyproject.toml)
- **Debug-Script:** `scripts/debug_senate_ajax.py` – DataTables JS-Config aus HTML extrahiert
- Dokumentation aktualisiert: DATA_SOURCES.md, DECISIONS.md (5 neue Entscheidungen), ROADMAP.md, ARCHITECTURE.md, CLAUDE.md, README.md

### Session 13 – 15. April 2026 – Weltuhr-Tooltip
- **Feature:** Hover-Tooltip auf jedem Weltuhr-Eintrag zeigt Börsen-Infos:
  - Exchange-Name (Xetra, NYSE/NASDAQ, LSE, TSE)
  - Handelszeiten mit Zeitzone (z.B. „09:00 – 17:30 CET")
  - Echtzeit-Status: „● Geöffnet" (cyan) / „● Geschlossen" (dim)
- **Design:** Precision Architect konform – `surface-high` Hintergrund, kein Border, Monospace-Font, Pfeil-Indikator, 150ms Fade-In Animation
- Dokumentation aktualisiert: ROADMAP.md

### Session 14 – 15. April 2026 – Fundamentaldaten-Qualität & Auto-Onboarding
- **Bug: Dividend Yield 95% statt 0.92%** – yfinance liefert `dividendYield` in Prozent-Form (0.92 = 0.92%), alle anderen Ratio-Felder als Dezimal (0.451 = 45.1%). Frontend multiplizierte blindlings *100
  - **Fix:** Normalisierung im Collector (`/100` bei Speicherung)
  - **Migration 013:** Bestehende DB-Werte rückwirkend korrigiert (`UPDATE ... / 100 WHERE > 0.25`)
- **Feature: Plausibilitätsprüfung für alle 17 Fundamental-Felder**
  - Jedes Feld hat definierte plausible Ranges (z.B. Div Yield 0–25%, PE 0–2000, Beta -3 bis 5)
  - Werte außerhalb der Range → `None` + WARNING im Log
  - Schützt gegen yfinance-Format-Änderungen und Yahoo-Datenqualitätsprobleme
  - `revenue_ttm` ausgenommen (ADRs melden in Lokalwährung, z.B. TSM in TWD)
- **Feature: Auto-Universe-Expansion + Auto-Backfill** (neuer Service `NewTickerOnboarder`)
  - Ticker aus Politiker-Trades und ARK-Holdings werden automatisch dem Universum hinzugefügt
  - Nach Hinzufügen: Automatischer Backfill (Preise 4J, TA-Indikatoren, Fundamentals, Sektor)
  - Behebt: SIRI und andere Ticker hatten keine Preise/Indikatoren/Fundamentals
  - ARK-Collector: Alte `_expand_universe()` durch zentralen Onboarder ersetzt (+ Backfill)
  - Form4: Keine Änderung nötig (universe-driven, entdeckt keine neuen Ticker)
- **Feature: Log-Zeilen Capture + UI-Anzeige** (Migration 014)
  - `CollectorLogCapture` Handler fängt WARNING/ERROR + collector-spezifische INFO-Zeilen
  - Gespeichert in neuer `log_lines` JSONB-Spalte auf `collection_log`
  - Detail-Modal im Dashboard: Aufklappbarer "Log-Zeilen"-Bereich mit farbcodierten Einträgen
  - Tabelle: Warning-Count in "Details"-Spalte für schnelle Anomalie-Erkennung
- Dokumentation aktualisiert: ROADMAP.md, DECISIONS.md, LEARNINGS.md, ARCHITECTURE.md
