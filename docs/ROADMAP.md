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

### Sprint 1 – Price Collector (yfinance)

**Ziel:** Täglicher OHLCV-Download für das gesamte Universum.

**Aufgaben:**
- [ ] `BaseCollector`-Klasse definieren (Abstract)
- [ ] `PriceCollectorYFinance` implementieren
- [ ] Batch-Fetching (nicht pro Ticker einzeln)
- [ ] Rate-Limiting und Retry-Logik
- [ ] Upsert-Pattern für `prices_daily`
- [ ] Logging in `collection_log`
- [ ] Unit-Tests mit Mock-Daten
- [ ] APScheduler-Job für tägliche Ausführung

**Definition of Done:** Nach manueller Ausführung sind OHLCV-Daten für alle Universums-Titel der letzten 10 Handelstage in der DB.

---

### Sprint 2 – ARK Holdings Tracker

**Ziel:** Tägliche Snapshots aller aktiven ARK-ETFs.

**Aufgaben:**
- [ ] ARK-Funds-Download-URLs recherchieren und dokumentieren
- [ ] CSV-Parser mit Robustheit gegen Format-Änderungen
- [ ] `ARKHoldingsCollector` implementieren
- [ ] Automatische Universum-Erweiterung (neue Titel werden angelegt)
- [ ] `ARKDeltaComputer` für Derived Layer
- [ ] Tests mit realen ARK-CSVs als Fixtures
- [ ] Error-Handling: Was wenn ARK mal keine Datei publiziert?

**Definition of Done:** Nach einer Woche Laufzeit enthält die DB 7 Tages-Snapshots aller ARK-ETFs, und die Delta-Tabelle zeigt sinnvolle Veränderungen.

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
