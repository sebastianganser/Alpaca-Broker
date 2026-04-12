# ROADMAP.md – Sprint-Planung & Fortschritt

> **Lebendes Dokument.** Wird nach jedem Sprint aktualisiert.
> Hier siehst du auf einen Blick, was erledigt ist, was gerade läuft und was als Nächstes kommt.

---

## Status-Übersicht

| Sprint | Titel | Status | Datum |
|---|---|---|---|
| 0 | Fundament (Docker, DB, Struktur) | 🟢 Erledigt | April 2026 |
| 1 | Price Collector (yfinance) | 🔴 Offen | – |
| 2 | ARK Holdings Tracker | 🔴 Offen | – |
| 3 | SEC EDGAR (Form 4 + 13F) | 🔴 Offen | – |
| 4 | Politiker-Trades (Capitol Trades) | 🔴 Offen | – |
| 5 | Fundamentals + Analyst-Daten | 🔴 Offen | – |
| 6 | Technische Indikatoren | 🔴 Offen | – |
| 7 | Feature Pipeline | 🔴 Offen | – |
| 8 | Monitoring & Reporting | 🔴 Offen | – |
| **⏸ Wartephase** | **2–3 Monate Datensammlung** | **–** | **–** |
| 9 | Erste explorative Analyse (Jupyter) | 🔴 Offen | – |
| 10 | Signal-Scoring-Modelle | 🔴 Offen | – |
| 11 | Backtest-Framework | 🔴 Offen | – |
| 12 | Paper-Trading-Integration | 🔴 Offen | – |

**Legende:** 🔴 Offen · 🟡 In Arbeit · 🟢 Erledigt · ⏸ Pausiert

---

## Aktueller Sprint

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
- [ ] Deployment auf Unraid testen
- [ ] Grundlegender Health-Check-Endpoint (später, wenn API kommt)

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

### Sprint 3 – SEC EDGAR

**Ziel:** Form 4 Insider-Trades und Form 13F institutionelle Holdings.

**Aufgaben:**
- [ ] SEC EDGAR API-Zugang dokumentieren (User-Agent-Header beachten!)
- [ ] `Form4Collector` – parsen der XML-Filings
- [ ] `Form13FCollector` – parsen der 13F-HR-Filings
- [ ] Dedup-Logik (gleiches Filing nicht doppelt importieren)
- [ ] `InsiderClusterComputer` für Cluster-Erkennung
- [ ] Mapping von CIK zu Ticker (über companyfacts-API)
- [ ] Tests mit echten EDGAR-Fixtures

**Definition of Done:** Tägliche Form-4-Erfassung läuft, historische 13F-Filings der Top-20-Institutionellen sind importiert.

---

### Sprint 4 – Politiker-Trades

**Ziel:** Als zusätzliche Signalquelle, trotz bekannter Verzögerungen.

**Aufgaben:**
- [ ] Quelle wählen: Capitol Trades (Scraping) vs. Quiver Quantitative (API) vs. andere
- [ ] Rechtliche Aspekte des Scrapings prüfen (Terms of Service)
- [ ] `PoliticianTradesCollector` implementieren
- [ ] Mapping auf Universum-Ticker
- [ ] Tests

**Definition of Done:** Politiker-Trades werden regelmäßig erfasst und sind in der DB abrufbar.

---

### Sprint 5 – Fundamentals + Analyst-Daten

**Ziel:** P/E, P/S, Revenue-Growth, Analyst-Ratings, Earnings-Kalender.

**Aufgaben:**
- [ ] `FundamentalsCollectorYF` mit yfinance
- [ ] `AnalystRatingsCollector`
- [ ] `EarningsCalendarCollector`
- [ ] Täglicher Job, aber Fundamentals nur 1x pro Woche (reicht meistens)
- [ ] Tests

**Definition of Done:** Für jeden Universum-Titel gibt es aktuelle Fundamentals, Ratings und Earnings-Termine.

---

### Sprint 6 – Technische Indikatoren

**Ziel:** Berechnung aller gängigen TA-Indikatoren aus den Preisdaten.

**Aufgaben:**
- [ ] `TechnicalIndicatorsComputer` mit `ta-lib` oder `pandas-ta`
- [ ] SMA, EMA, RSI, MACD, Bollinger, ATR
- [ ] Relative Strength vs. SPY
- [ ] Täglicher Job nach Price-Collector
- [ ] Tests mit bekannten Werten

**Definition of Done:** Für jeden Titel und jeden Handelstag sind die TA-Indikatoren berechnet und in `technical_indicators` gespeichert.

---

### Sprint 7 – Feature Pipeline ⭐

**Ziel:** Das Herzstück – aggregiere alle Rohdaten und Derived-Daten in `feature_snapshots`.

**Aufgaben:**
- [ ] `FeaturePipeline`-Klasse
- [ ] Aggregations-Logik für jede Feature-Gruppe
- [ ] ARK-Conviction-Score-Berechnung
- [ ] Insider-Cluster-Score-Berechnung
- [ ] Analyst-Consensus-Score
- [ ] Target-Variablen-Nachtragung (1d, 5d, 20d, 60d Returns)
- [ ] Täglicher Job nach allen Collectors und Derived-Computern
- [ ] Tests mit End-to-End-Szenarien

**Definition of Done:** Für jeden Titel im Universum gibt es für den aktuellen Tag einen vollständigen Feature-Vektor. Zielvariablen werden nach 1/5/20/60 Tagen automatisch nachgetragen.

---

### Sprint 8 – Monitoring & Reporting

**Ziel:** Transparenz darüber, was das System tut.

**Aufgaben:**
- [ ] Daily Summary E-Mail oder Telegram (wie läuft die Datensammlung?)
- [ ] Health-Check-Endpoint
- [ ] Warnung bei fehlenden Daten (z.B. ARK-Download fehlgeschlagen)
- [ ] Einfaches Streamlit- oder HTML-Dashboard für Datenübersicht
- [ ] `LEARNINGS.md` automatisch mit DB-Metriken füllen

**Definition of Done:** Sebastian bekommt täglich eine Zusammenfassung und merkt sofort, wenn etwas hakt.

---

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
- Grundsatzentscheidung: Organisches Datenwachstum (kein Backfill), Dynamische Feature-Aktivierung
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
- Nächster Schritt: **Sprint 3 (SEC Insider Trades)**
