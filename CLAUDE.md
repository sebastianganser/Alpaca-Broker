# CLAUDE.md – Alpaca-Broker Project

> **Einstiegspunkt für jede Claude-Session zu diesem Projekt.**
> Lies dieses Dokument zuerst, dann bei Bedarf die verlinkten Detail-Dokumente.

---

## Projekt-Identität

**Projektname:** `Alpaca-Broker` (Python-Paket: `trading-signals`)
**Owner:** Sebastian
**Gestartet:** April 2026
**Repository:** [github.com/sebastianganser/Alpaca-Broker](https://github.com/sebastianganser/Alpaca-Broker) (privat)
**Projekt-Root lokal:** `D:\Sebastian\Dokumente\Privat\Rudi\Coding\Workspaces\Alpaca-Broker`
**Deployment-Ziel:** Unraid-Server (Docker), separate PostgreSQL 18 (`postgresql18-alpaca`, Port 5435)

---

## Projekt-Mission in einem Satz

> Aufbau eines eigenen **Signal Warehouse**, das täglich so viele relevante Marktdaten und "Smart Money"-Signale wie möglich sammelt, um später datenbasiert robuste Trading-Strategien im Alpaca Paper Trading zu entwickeln.

## Was dieses Projekt NICHT ist (wichtig!)

- ❌ **Kein Live-Trading-System** – alles läuft ausschließlich im Alpaca **Paper-Trading-Konto**
- ❌ **Keine Finanzberatung, keine Alpha-Garantie** – dies ist ein Lern- und Forschungsprojekt
- ❌ **Kein "Claude handelt autonom"-System** – LLMs werden nur dort eingesetzt, wo natürliche Sprache echten Mehrwert bringt
- ❌ **Kein Crypto-Trading** (zumindest nicht in Phase 1)
- ❌ **Keine Options-Strategien am Anfang** – Wheel-Strategie wurde bewusst nach hinten geschoben zugunsten solider Datenbasis

---

## Die drei Grundprinzipien

### 1. Daten sammeln vor Daten nutzen
Wir starten **nicht** mit einer Trading-Strategie. Wir starten mit einem Datensammler, der mindestens 2–3 Monate lang **ohne zu handeln** nur Daten in die Datenbank schreibt. Erst wenn wir genug Material haben, fangen wir an, daraus Signale zu destillieren.

### 2. Trennung von Rohdaten und Bewertung
Rohdaten sind heilig und werden nie verändert (append-only). Bewertungen, Scores und Signale werden aus den Rohdaten **berechnet** und sind jederzeit neu berechenbar, wenn sich der Algorithmus ändert.

### 3. Deterministischer Kern, LLM nur am Rand
Die kritischen Pfade (Daten-Fetching, Berechnungen, später Order-Ausführung) sind reiner Python-Code mit Unit-Tests. LLMs kommen nur bei unstrukturierten Aufgaben zum Einsatz (News parsen, Reports generieren, Ad-hoc-Analysen).

---

## Aktueller Status

**Phase:** 🟢 Sprint 7 abgeschlossen + Produktionsbetrieb
**Aktueller Sprint:** Operational – System läuft auf Unraid, Datensammlung aktiv
**Nächster Schritt:** Sprint 8 (Feature Pipeline)
**Letzte Aktualisierung:** 15. April 2026
**Deployment:** ✅ Unraid Docker (192.168.1.93:8090)

Siehe [ROADMAP.md](docs/ROADMAP.md) für den detaillierten Fortschritt.

---

## Quick Facts für jede Session

| Eigenschaft | Wert |
|---|---|
| **Sprache** | Python 3.12+ |
| **Backend-API** | FastAPI (im gleichen Prozess wie APScheduler) |
| **Frontend** | Vite + React SPA (Stitch "Precision Architect" Design) |
| **ORM** | SQLAlchemy 2.0 |
| **Migrations** | Alembic |
| **Datenbank** | PostgreSQL 18 (`postgresql18-alpaca`, 192.168.1.93:5435, DB: `broker_data`, Schema: `signals`) |
| **Paketmanager** | uv (Python), npm (Frontend) |
| **Scheduler** | APScheduler (im Python-Prozess) |
| **Preisdaten** | Alpaca Market Data API (IEX feed, Multi-Symbol-Batch) |
| **Universe** | ~845 aktive Ticker (S&P 500 + Nasdaq 100 + ARK) |
| **Deployment** | Docker Compose auf Unraid (1 Container: Collector + API + UI, Port 8090) |
| **Scheduler** | APScheduler (10 Jobs: 5 täglich, 4 wöchentlich, 1 monatlich inkl. Sektor-Enrichment) |
| **Broker (später)** | Alpaca Paper Trading (NIEMALS Live!) |
| **Versionskontrolle** | Git, [GitHub (sebastianganser/Alpaca-Broker)](https://github.com/sebastianganser/Alpaca-Broker) |

---

## Modell-Routing (für LLM-Aufgaben)

| Aufgabe | Modell | Wo |
|---|---|---|
| Architektur-Design, Edge-Case-Analyse | Opus 4.6 | Claude Desktop (sparsam!) |
| Standard-Implementierung, Debugging | Sonnet 4.6 | Claude Desktop (Default) |
| News-Parsing, Daily Reports | Haiku 4.5 | API (Scheduler-Jobs) |
| Routine-Scheduler (Preis-Checks etc.) | **KEIN LLM** | Python-Code |

**Kostenerwartung im Vollbetrieb:** ~20 €/Monat Claude Pro + ~10–15 $/Monat API-Kosten

---

## Wichtige Dokumente im Projekt

| Dokument | Zweck | Änderungsfrequenz |
|---|---|---|
| **CLAUDE.md** (dieses Dokument) | Einstiegspunkt, Projekt-Identität | Selten |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Technische Architektur, DB-Schema, Datenflüsse | Bei Strukturänderungen |
| [ROADMAP.md](docs/ROADMAP.md) | Sprint-Planung, Status, nächste Schritte | **Nach jedem Sprint** |
| [DATA_SOURCES.md](docs/DATA_SOURCES.md) | Katalog aller Datenquellen mit Details | Wenn neue Quellen dazukommen |
| [DECISIONS.md](docs/DECISIONS.md) | Decision Log – Warum haben wir X so entschieden? | **Bei jeder wichtigen Entscheidung** |
| [LEARNINGS.md](docs/LEARNINGS.md) | Erkenntnisse aus den gesammelten Daten | Kontinuierlich, sobald Daten da sind |

---

## Session-Startchecklist für Claude

Wenn du (Claude) eine neue Session zu diesem Projekt startest, arbeite diese Checkliste ab:

1. ✅ **Lies CLAUDE.md komplett** (dieses Dokument)
2. ✅ **Lies ROADMAP.md** – wo stehen wir gerade? Welcher Sprint ist aktiv?
3. ✅ **Scanne DECISIONS.md** – gibt es kürzliche Entscheidungen, die für den aktuellen Kontext relevant sind?
4. ✅ **Lies das für den aktuellen Sprint relevante Kapitel** in ARCHITECTURE.md
5. ✅ **Frage Sebastian**, was das Ziel der aktuellen Session ist, bevor du loslegst

## Session-Endechecklist für Claude

Am Ende jeder produktiven Session:

1. ✅ **Update ROADMAP.md** – was wurde gemacht, was ist der nächste Schritt?
2. ✅ **Neue Entscheidungen in DECISIONS.md** dokumentieren
3. ✅ **Neue Erkenntnisse in LEARNINGS.md** festhalten (falls zutreffend)
4. ✅ **ARCHITECTURE.md aktualisieren**, wenn sich Struktur geändert hat

---

## Wichtige Grenzen & Sicherheitsregeln

### 🚨 Niemals ohne explizite Bestätigung

- **Niemals** Live-Trading aktivieren (Hardcoded Check auf `paper-api.alpaca.markets`)
- **Niemals** echte Orders platzieren ohne manuelle Freigabe
- **Niemals** Credentials in Git committen (`.env`-Dateien in `.gitignore`)
- **Niemals** die GynOrg-Datenbank berühren (strikte Trennung)

### Verantwortungsbereich

Dieses Projekt ist **privat und experimentell**. Alle Entscheidungen trifft Sebastian. Claude assistiert, implementiert und berät – aber die finale Verantwortung für jeden Trade und jede Konfiguration liegt beim Menschen.

---

## Kontakt zur übergeordneten Projektlandschaft

Sebastian arbeitet parallel an anderen Projekten. Dieses Projekt ist **strikt getrennt** von:
- **GynOrg** (gynäkologische Klinikverwaltung, eigene PostgreSQL)
- **WoSZ** (Wardens of Sector Zero, Strategiespiel-Konzept)
- **Sonstige Coding-Experimente**

Trading-Signals hat seinen eigenen Ordner, seine eigene DB, sein eigenes Docker-Compose.
