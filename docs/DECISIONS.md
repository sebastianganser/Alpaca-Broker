# DECISIONS.md – Decision Log

> Chronologisches Protokoll aller wichtigen Projektentscheidungen.
> Jede Entscheidung wird mit **Kontext, Optionen, Entscheidung und Begründung** dokumentiert.
> So verstehen wir in Monaten noch, warum wir welche Wahl getroffen haben.

---

## Template für neue Einträge

```markdown
### [YYYY-MM-DD] Titel der Entscheidung

**Kontext:** Was war die Situation, die eine Entscheidung nötig machte?

**Optionen:**
- Option A: ...
- Option B: ...
- Option C: ...

**Entscheidung:** Welche Option wurde gewählt?

**Begründung:** Warum?

**Revisit-Trigger:** Unter welchen Umständen würden wir das nochmal überdenken?
```

---

## Entscheidungen

### [2026-04-11] Kein Trading zu Beginn – Signal Warehouse first

**Kontext:** Die ursprüngliche Video-Vorlage schlug vor, direkt mit Trading-Strategien (Trailing Stop, Wheel, Copy Trading) zu starten. Nach der Diskussion wurde klar, dass ohne belastbare Datenbasis jede Strategie auf Bauchgefühl beruht.

**Optionen:**
- A: Sofort mit Trading starten (wie im Video), Strategien iterativ verbessern
- B: Zuerst 2–3 Monate nur Daten sammeln, dann auf Basis echter Daten Strategien entwickeln
- C: Parallel sammeln und handeln

**Entscheidung:** Option B – erst Signal Warehouse, dann Strategien.

**Begründung:**
- Datensammeln kostet quasi nichts, aber vergangene Daten sind nicht nachträglich beschaffbar
- Backtests brauchen saubere Daten, die wir zuerst aufbauen müssen
- Ohne Verständnis der Signalqualität würden wir blind kopieren (z.B. ARK-Rebalances mit Conviction-Käufen verwechseln)
- Robustheit wurde als Top-Priorität genannt – das verlangt datenbasierte Entscheidungen

**Revisit-Trigger:** Wenn nach 2 Monaten klar wird, dass die gesammelten Daten zu dünn sind, überdenken wir die Strategie-Entwicklung.

---

### [2026-04-11] Datenstrategie: Maximal sammeln, später filtern

**Kontext:** Die Frage, welche Datenquellen einbezogen werden sollen. Gefahr der "Signal-Overload"-Falle – zu viele Signale könnten jeden Trade blockieren.

**Optionen:**
- A: Minimal starten (nur ARK + Preise)
- B: Moderate Auswahl (ARK + Form 4 + Preise)
- C: Maximal sammeln, statistisch filtern
- D: ML-first-Ansatz mit riesigem Feature-Store

**Entscheidung:** Option C – maximal sammeln, aber später mit Feature-Selection-Algorithmen die relevanten Signale finden.

**Begründung:**
- Sebastian wollte die maximale Datenmenge, aber keine Signal-Overload-Falle
- Feature-Selection-Verfahren (Korrelation, LASSO, Random Forest Importance) lösen das Overload-Problem datenbasiert
- Speicherplatz ist billig, nachträgliche Datensammlung unmöglich
- Die "Sammeln vs. Nutzen"-Trennung ist ein etabliertes Muster in quantitativer Finanzforschung (Feature Store)

**Revisit-Trigger:** Falls Datenbank performanceprobleme bekommt oder einzelne Quellen sich als komplett unbrauchbar herausstellen.

---

### [2026-04-11] Universum: Dynamisch mit Signalen wachsend

**Kontext:** Welche Aktien sollen überhaupt getrackt werden? Optionen reichten von 200 Titeln bis Russell 3000 (3000 Titel).

**Optionen:**
- A: Nur ARK-Titel (~200)
- B: S&P 500 + ARK
- C: Russell 3000 (sehr breit)
- D: Dynamisch wachsend

**Entscheidung:** Option D – Startuniversum aus ARK-Titeln und S&P 100, dann organisches Wachstum durch neue Signale.

**Begründung:**
- Vermeidet unnötigen Overhead durch Titel ohne Signale
- Das Universum spiegelt automatisch wider, wo die Smart Money aktiv ist
- Wächst natürlich über Zeit, bleibt aber fokussiert
- Neue Signalquellen (Form 4, Politiker) erweitern das Universum automatisch

**Revisit-Trigger:** Falls sich herausstellt, dass wichtige Titel systematisch fehlen (z.B. weil sie in keiner Signalquelle auftauchen).

---

### [2026-04-11] Datenbank: PostgreSQL 18 in separatem Container

**Kontext:** Wo sollen die Daten gespeichert werden? Option war, die bestehende GynOrg-PostgreSQL zu nutzen.

**Optionen:**
- A: GynOrg-PostgreSQL mitnutzen (gleiche Instanz, anderes Schema)
- B: Separater PostgreSQL-Container
- C: SQLite (deutlich einfacher, aber limitiert)

**Entscheidung:** Option B – separater Container.

**Begründung:**
- Strikte Trennung zwischen medizinischer Anwendung (GynOrg) und privatem Trading-Experiment
- Unabhängige Backups und Wartungszyklen
- Keine Gefahr, dass Trading-Workloads die Klinik-DB beeinflussen
- Datenmengen wachsen stetig (geschätzt 3–5 GB nach 2 Jahren), lieber getrennt
- Datenschutz: Medizinische Daten und Finanzdaten sollen nicht in einer DB liegen

**Revisit-Trigger:** Nie – diese Trennung ist unumstößlich.

---

### [2026-04-11] Architektur: Deterministic Core, LLM nur am Rand

**Kontext:** Wie stark sollen LLMs in den kritischen Pfaden eingesetzt werden?

**Optionen:**
- A: LLM-heavy – Claude trifft viele Entscheidungen autonom
- B: Hybrid – Kerncode deterministisch, LLM für spezifische Aufgaben
- C: LLM-frei – alles klassischer Code

**Entscheidung:** Option B – deterministischer Kern mit LLM-Aufrufen nur für unstrukturierte Aufgaben.

**Begründung:**
- Trading-Entscheidungen dürfen nicht von LLM-Halluzinationen abhängen
- Unit-Tests und Backtests sind nur mit deterministischem Code möglich
- Kostenoptimal: LLM-Aufrufe nur wo nötig
- LLMs sind stark bei: Text-Parsing, Zusammenfassungen, kreativer Strategieentwicklung
- LLMs sind schwach bei: Numerischer Präzision, reproduzierbaren Ergebnissen, Rechenzeit

**LLM-Einsatz nur bei:**
- News- und Text-Parsing (Haiku)
- Daily-Report-Generierung (Haiku)
- Ad-hoc-Analysen im Claude Desktop (Sonnet/Opus)
- Strategische Entscheidungshilfen für Sebastian (Opus)

**Revisit-Trigger:** Falls neue LLM-Fähigkeiten (z.B. zuverlässige Tool-Use-Ketten) das Kosten-Nutzen-Verhältnis deutlich verändern.

---

### [2026-04-11] Datenhaltung: Append-only Raw Layer, berechnete Derived Layer

**Kontext:** Sollen Daten direkt als "fertige" Features gespeichert werden oder roh?

**Optionen:**
- A: Nur fertige Features (kompakt, aber verlustbehaftet)
- B: Nur Rohdaten (flexibel, aber rechenintensiv)
- C: Zwei-Schicht-Modell: Raw + Derived

**Entscheidung:** Option C – Raw Layer ist append-only und heilig, Derived Layer wird bei Bedarf neu berechnet.

**Begründung:**
- Wenn sich das Berechnungsmodell ändert (z.B. neuer Conviction-Score), können wir komplett neu rechnen
- Raw-Daten sind der "Single Source of Truth" – nachvollziehbar und auditierbar
- Performance: Derived Layer kann indexiert und optimiert werden
- Storage-Kosten sind vernachlässigbar

**Revisit-Trigger:** Falls Derived Layer zu groß wird (unwahrscheinlich).

---

### [2026-04-11] Broker: Alpaca Paper Trading, niemals Live

**Kontext:** Welcher Broker für die Trading-Phase?

**Optionen:**
- A: Alpaca (US-basiert, gute API, Paper-Trading-Modus)
- B: Interactive Brokers (komplexer, aber mächtiger)
- C: Lokaler Backtest-only ohne Broker

**Entscheidung:** Alpaca Paper Trading mit Hardcoded Live-Trading-Sperre.

**Begründung:**
- Alpaca hat das mit Abstand beste Paper-Trading-Erlebnis
- API ist sauber dokumentiert und in Python gut unterstützt (`alpaca-py`)
- Kostenlos für Paper-Trading
- Hardcoded-Check auf `paper-api.alpaca.markets` verhindert versehentliches Live-Trading
- Keine realen Verluste möglich in der Lern- und Entwicklungsphase

**Wichtige Sicherheitsregel:**
```python
assert "paper" in settings.ALPACA_ENDPOINT, \
    "SAFETY: Live trading is not allowed in this system"
```

**Revisit-Trigger:** Nur nach mindestens 12 Monaten erfolgreichem Paper-Trading mit dokumentierter Outperformance gegen Benchmark. Und auch dann nur mit extremer Vorsicht.

---

### [2026-04-11] Dokumentationsstrategie: CLAUDE.md + docs/-Ordner

**Kontext:** Wie soll die Projektdokumentation strukturiert werden?

**Optionen:**
- A: Eine einzige riesige README.md
- B: CLAUDE.md (Einstieg) + docs/-Ordner (Details)
- C: Wiki auf GitHub
- D: Externes Tool (Notion, Obsidian)

**Entscheidung:** Option B – `CLAUDE.md` als Einstiegspunkt, `docs/`-Ordner für Detaildokumente.

**Begründung:**
- `CLAUDE.md` folgt Sebastians etabliertem Muster aus Claude Code
- Getrennte Dateien sind besser versionierbar und diff-bar
- Ein Einstiegspunkt erleichtert neuen Claude-Sessions den Wiedereinstieg
- Alles in Git → immer dabei, kein zusätzliches Tool nötig
- Obsidian wäre redundant zu Sebastians MainBrain-Local-Vault

**Dokumentstruktur:**
- `CLAUDE.md` – Einstiegspunkt (kurz, pragmatisch)
- `docs/ARCHITECTURE.md` – Technische Details (lang, stabil)
- `docs/ROADMAP.md` – Sprint-Status (lebend)
- `docs/DATA_SOURCES.md` – Datenquellen-Katalog (wächst mit)
- `docs/DECISIONS.md` – Dieses Dokument (wächst mit)
- `docs/LEARNINGS.md` – Erkenntnisse (wächst mit, sobald Daten da sind)

**Revisit-Trigger:** Falls das Projekt einen bestimmten Umfang überschreitet oder mehrere Mitwirkende bekommt.

---

### [2026-04-11] Tempo der Implementierung: Dokumentation zuerst, dann schrittweise

**Kontext:** Wie schnell soll das Projekt umgesetzt werden?

**Optionen:**
- A: Sofort alles programmieren (Sprint 0–8 in einem Rutsch)
- B: Schrittweise mit Dokumentation
- C: Dokumentation komplett fertigstellen, dann erst Code

**Entscheidung:** Option C – zunächst vollständige Projektdokumentation, dann schrittweise Sprint-Umsetzung.

**Begründung:**
- Sebastian hat explizit darum gebeten
- Dokumentation erzwingt klares Denken über die gesamte Architektur
- Vermeidet spätere Refactorings durch übersehene Aspekte
- Ermöglicht sauberen Wiedereinstieg in zukünftigen Sessions
- Die Zeit für Dokumentation ist gut investiert und spart später das Mehrfache

**Revisit-Trigger:** Falls die Dokumentation zu theoretisch wird und ein praktischer Test nötig ist.

---


### [2026-04-12] Neun offene technische Entscheidungen – alle festgezurrt

**Kontext:** Nach Abschluss der initialen Dokumentation standen noch neun offene technische Entscheidungen an. Sebastian bat um strukturierte Empfehlungen, dann erfolgte Zustimmung zu allen neun.

**Entscheidungen im Überblick:**

1. ~~**Politiker-Trades-Quelle → Quiver Quantitative API**~~ → **Überholt** (siehe Entscheidung vom 2026-04-13: Senate eFD statt Quiver, Constraint: kostenlos bleiben)
   Ursprüngliche Begründung: Offizielle API, saubere JSON-Responses. Später verworfen wegen Kosten (30 $/Monat).

2. **TA-Library → pandas-ta** (statt ta-lib)
   Begründung: Reines Python, installiert sich ohne C-Compiler-Drama. Performance ist für EOD-Daten völlig ausreichend. Robustheit schlägt Geschwindigkeit bei täglichen Berechnungen auf ~600 Titeln.

3. **Paket-Manager → uv** (statt Poetry)
   Begründung: 10–100x schneller als Poetry, verwaltet Python-Versionen mit, kompatibel mit Standard-pyproject.toml. Astral hat sich 2024/2025 als neuer Standard etabliert. Kein Legacy-Grund für Poetry bei einem Neustart.

4. **Scheduler → APScheduler** (statt Cron)
   Begründung: Ein langlebiger Python-Container, gemeinsames Logging und Config, Job-Dependencies modellierbar, dynamische Steuerung aus dem Code. Cron würde Kontext und Error-Handling fragmentieren.

5. **Monitoring → Telegram + E-Mail-Fallback**
   Begründung: Telegram-Bot für Push-Nachrichten (Daily Summary, akute Warnungen), E-Mail als zweiter Kanal für kritische Fehler und archivierbare Reports. Python-telegram-bot ist trivial einzurichten.

6. **Dashboard → FastAPI + Vite/React SPA** (statt Streamlit – siehe Entscheidung weiter unten)
   Begründung: Volle Design-Kontrolle für das Stitch „Precision Architect" Design System. FastAPI läuft im gleichen Prozess wie APScheduler. React SPA wird als statische Dateien von FastAPI auf Port 8090 ausgeliefert.

7. **Feature-Selection → Alle drei Methoden parallel**
   Begründung: Korrelation (lineare Zusammenhänge), LASSO (automatische Feature-Auswahl), Random Forest Importance (nicht-lineare Muster). Wenn ein Feature in allen drei Methoden als wichtig erkannt wird, ist das ein robustes Signal. Implementierungsaufwand ist minimal, Erkenntnisgewinn hoch.

8. **Scoring-Modell → Schrittweise Evolution**
   Phase 1 (Start): Gleichgewichtete Features → einzig ehrlicher Startpunkt ohne Performance-Historie
   Phase 2 (nach 3 Monaten): Performance-gewichtet basierend auf beobachteter Korrelation mit Returns
   Phase 3 (nach 12 Monaten): ML-Modelle wie XGBoost, wenn genug Trainingsdaten vorhanden sind
   Begründung: Overfitting-Risiko bei zu frühem ML-Einsatz. Ehrliche Baseline zuerst.

9. **Portfolio-Konstruktion → Konservative Defaults**
   - Max 20 Positionen gleichzeitig (Diversifikation ohne Overhead)
   - Gleichgewichtet, 5% pro Position
   - Max 5% Einzelposition als hardcoded Guardrail
   - Wöchentliches Rebalancing (weniger Transaktionskosten, weniger Rauschen)
   - Stop-Loss bei -10%, Take-Profit bei +25%
   Begründung: Kelly-Sizing oder Risk-Parity kommen erst, wenn belastbare Performance-Daten vorliegen. Simple, robuste Defaults zum Start.

**Revisit-Trigger:** 
- ~~Punkt 1: Falls Quiver Free Tier nicht ausreicht~~ → Erledigt: Senate eFD gewählt (kostenlos)
- Punkt 7: Nach ersten Analyse-Ergebnissen, falls eine Methode durchgehend bessere Resultate liefert
- Punkt 8: An den Phasengrenzen (3 Monate, 12 Monate) automatisch
- Punkt 9: Nach 3 Monaten Paper-Trading basierend auf tatsächlicher Performance

---

### [2026-04-12] DB-Verbindungsdaten: Anpassung an bestehende Infrastruktur

**Kontext:** Die ARCHITECTURE.md sah ursprünglich `trading_signals` als DB-Name und `signals_rw` als User vor. Auf Unraid wurde jedoch bereits eine PostgreSQL-18-Instanz mit abweichenden Parametern eingerichtet.

**Entscheidung:** Wir passen uns an die bestehende Infrastruktur an:
- Container: `postgresql18-alpaca`
- DB-Name: `broker_data` (statt `trading_signals`)
- User: `sebastian` (statt `signals_rw`)
- Port: `5435` (extern)
- Volume: `/mnt/user/Datafolder/Broker/`

Die logische Trennung erfolgt über das Schema `signals` innerhalb der `broker_data`-DB.

**Begründung:** Pragmatismus – die DB existiert bereits, Umbenennen bringt keinen Mehrwert. Das Schema `signals` sorgt für die nötige Isolation.

**Revisit-Trigger:** Nie – die DB läuft, die Verbindung steht.

---

### [2026-04-12] Repo-Name `Alpaca-Broker` statt `trading-signals`

**Kontext:** Sebastian hat das GitHub-Repo als `Alpaca-Broker` angelegt, nicht als `trading-signals`.

**Entscheidung:** Repo heißt `Alpaca-Broker`, das Python-Paket intern bleibt `trading-signals`.

**Begründung:** Rein kosmetisch, kein Einfluss auf Funktionalität. Umbenennung wäre unnötiger Aufwand.

---

### Entscheidung: Organisches Datenwachstum statt historischem Backfill

**Datum:** 12. April 2026
**Sprint:** 1

**Kontext:** yfinance bietet 2+ Jahre historische Daten kostenlos. Andere Quellen (SEC EDGAR, Quiver Quantitative, ARK) bieten keinen kostenlosen Zugang zu tiefer Historie.

**Entscheidung:** Kein Backfill. Alle Datenquellen starten ab dem gleichen Zeitpunkt und wachsen synchron/organisch mit.

**Begründung:** Synchrone Datenbasis über alle Quellen hinweg. Features wie SMA 200 erst aktivieren, wenn 200 Tage Daten vorliegen. Verhindert "Pseudo-Alpha" aus Backfill-Daten, die bei anderen Quellen nicht verfügbar sind.

---

### Entscheidung: Dynamische Feature-Aktivierung

**Datum:** 12. April 2026
**Sprint:** 1

**Kontext:** Ohne Backfill dauert es Monate, bis genug Daten für langfristige Indikatoren (SMA 200, 52-Wochen-Hoch) vorliegen.

**Entscheidung:** Jedes abgeleitete Feature definiert eine `min_data_days`-Schwelle. Das Feature wird erst in den Feature-Store geschrieben, wenn genug Daten vorhanden sind.

**Begründung:** Verhindert fehlerhafte Signale aus unvollständigen Daten. Features "schalten sich selbst ein" sobald die Datenbasis reicht.

---

### Entscheidung: Gap-Extrapolation mit Forward-Fill

**Datum:** 12. April 2026
**Sprint:** 1

**Kontext:** Bei ungeplanten Datenlücken (Server-Ausfall, API-Fehler) muss die Zeitreihe lückenlos bleiben, damit technische Indikatoren korrekt berechnet werden.

**Entscheidung:** 3-stufiger Prozess: (1) Lücken erkennen via NYSE-Kalender, (2) von Quelle nachladen, (3) Forward-Fill mit `is_extrapolated=TRUE`-Flag.

**Begründung:** Forward-Fill (letzter Close als Open/High/Low/Close, Volume=0) ist der konservativste Ansatz – erzeugt kein falsches Signal. Das Flag ermöglicht Downstream-Filtern in Analysen.

---

### Entscheidung: Alpaca als Source of Truth für Handelbarkeit

**Datum:** 12. April 2026
**Sprint:** 1 (Nacharbeit)

**Kontext:** WBA (Walgreens) lieferte keine yfinance-Daten. Unklar ob delisted, umbenannt oder temporärer Fehler.

**Entscheidung:** Die Alpaca Assets-API (`GET /v2/assets`) ist die autoritative Quelle für die Frage, ob ein Ticker handelbar ist. Nicht-handelbare Ticker werden im Universe deaktiviert. Script `scripts/validate_universe.py` prüft das.

**Begründung:** Wir können nur traden, was Alpaca anbietet. Daten für nicht-handelbare Titel zu sammeln wäre verschwendet. Alpaca liefert Status, Exchange und Name – besser als Raten.

---

### Entscheidung: Docker-Deployment erst nach allen Datenquellen + UI

**Datum:** 12. April 2026

**Kontext:** Der Collector ist funktionsfähig, aber Sebastian möchte die Datensammlung nachvollziehen können.

**Entscheidung:** Kein Docker-Deployment auf Unraid, bis (a) alle geplanten Datenquellen angebunden sind und (b) ein UI zur Übersicht existiert. Bis dahin laufen Collector-Tests lokal.

**Begründung:** Ein halb-fertiges System blind laufen zu lassen bringt keinen Nutzen. Besser: alle Quellen + Monitoring-UI, dann deployen.

---

### Entscheidung: arkfunds.io API statt ARK CSV-Scraping

**Datum:** 12. April 2026
**Sprint:** 2

**Kontext:** Die direkte CSV-URL `ark-funds.com/downloads/fund-holdings/{ETF}.csv` gibt 403/404 zurück (Cloudflare-Schutz). CSV-Scraping wäre fragil gegenüber Format-Änderungen.

**Entscheidung:** arkfunds.io JSON-API als Datenquelle für ARK-Holdings. Endpoint: `GET /api/v2/etf/holdings?symbol={ETF}`.

**Begründung:** Sauberes JSON statt CSV-Parsing. Kostenlos, keine Auth nötig, Swagger-Doku. Enthält zusätzlich `share_price` und `weight_rank`. Nachteil: Drittanbieter-Abhängigkeit (nicht offizielle ARK-Quelle).

---

### Entscheidung: Alpaca Market Data API statt yfinance

**Datum:** 12. April 2026
**Sprint:** 1b

**Kontext:** yfinance ist inoffiziell und kann jederzeit brechen. Wir handeln nur Alpaca-Instrumente – die Preisdaten sollten von der gleichen Quelle kommen.

**Entscheidung:** PriceCollectorAlpaca ersetzt PriceCollectorYFinance als primäre Preisdatenquelle. Multi-Symbol-Endpoint (`/v2/stocks/bars`) mit `adjustment=all` und `feed=iex`. yfinance bleibt als Code (Fallback), wird aber nicht mehr im Scheduler registriert.

**Begründung:** Offizielle API, stabil, Kurs-Konsistenz mit Trading-Plattform. `adj_close = close` (da adjustiert). Batch-Endpoint: 100 Ticker pro Request, 644 Ticker in 7 Requests (<10s).

---

### Entscheidung: Universe-Erweiterung auf S&P 500 + Nasdaq 100

**Datum:** 12. April 2026
**Sprint:** 1b

**Kontext:** Mit 102 Tickern (S&P 100 + SPY) fehlte viel Marktbreite. ARK-Titel uberlappen stark mit S&P 500/Nasdaq 100.

**Entscheidung:** Universe enthalt S&P 500 (503) + Nasdaq 100 (101) + ARK-Erganzungen. Wikipedia als Quelle fur Index-Listen (kostenlos, aktuell genug bei ~4 Rebalancings/Jahr). Neue `index_membership` ARRAY-Spalte in universe fur Filterung.

**Ergebnis:** 644 aktive Ticker. ~80% der ARK-Titel waren bereits abgedeckt.

---

### [2026-04-13] SEC EDGAR Zugriffsstrategie → Submissions API + XML-Parsing

**Kontext:** Form 4 (Insider-Trades) und 13F (institutionelle Holdings) müssen aus SEC EDGAR importiert werden. Mehrere Optionen: EFTS Global Search, Company Submissions API, oder Libraries wie `edgartools`.

**Optionen:**
1. **EFTS Full-Text Search** – Globale Suche nach neuen Form 4 Filings
2. **Submissions API** – Pro CIK die Recent Filings laden, dann Form 4 filtern
3. **edgartools Library** – Abstraktion über EDGAR, aber neue Dependency

**Entscheidung:** Submissions API + stdlib `xml.etree.ElementTree` für XML-Parsing. Kein neues Paket.

**Begründung:** Submissions API ist stabiler als EFTS und liefert direkt Accession Numbers + Primary Documents. `xml.etree.ElementTree` ist Teil der Standardbibliothek – keine zusätzliche Dependency nötig. Form 4 XML ist ausreichend einfach strukturiert.

---

### [2026-04-13] Form 4 Strategy → Universe-driven statt Global Search

**Kontext:** Sollen wir alle Form 4 Filings global durchsuchen oder nur für Ticker in unserem Universe?

**Optionen:**
1. **Globale Suche** – Alle Form 4 Filings der SEC (Tausende pro Tag)
2. **Universe-driven** – Nur Form 4 für unsere 644 aktiven Ticker

**Entscheidung:** Universe-driven mit Auto-Expansion.

**Begründung:** Bei 644 Tickern und ~10 req/s dauert das Durchlaufen ~65 Sekunden – akzeptabel für einen Daily-Job. Globale Suche würde massiv mehr Daten (und SEC-Requests) erzeugen. Auto-Expansion analog ARK: wenn ein signifikanter Insider-Trade einen neuen Ticker betrifft, kann er automatisch zum Universe hinzugefügt werden.

---

### [2026-04-13] Form 13F Strategy → Filer-driven (Top-20 Institutionelle)

**Kontext:** 13F filings sind quartalsweise und bis zu 45 Tage verzögert. Welche Filer tracken?

**Optionen:**
1. **Alle 13F-Filer** – Tausende von Institutionen
2. **Top-20 handverlesene Filer** – Buffett, Burry, Ackman, etc.
3. **Dynamisch basierend auf Universe** – Filer, die unsere Ticker halten

**Entscheidung:** Top-20 Filer als Python-Konstante (konfigurierbar).

**Begründung:** 13F hat ohnehin 45 Tage Verzögerung – kein taktisches Signal, sondern Kontextdaten. Die Top-20 bekannten "Smart Money" Investoren sind die interessantesten. Liste kann später erweitert werden.

---

### [2026-04-13] Politiker-Trades → Senate eFD Scraping (kostenlos) statt Quiver API

**Kontext:** Sprint 4 benötigt eine Datenquelle für US-Politiker-Trades.

**Optionen:**
1. **Quiver Quantitative API** – Saubere JSON-API, 30 $/Monat
2. **Capitol Trades Scraping** – Kostenlos, aber ToS-Grauzone
3. **Senate eFD + House Clerk** – Offizielle Regierungsportale, kostenlos
4. **Stock Watcher GitHub/S3** – Community-Projekt, kostenlos

**Entscheidung:** Senate eFD direkt scrapen (Option 3, nur Senate-Teil).

**Begründung:** Constraint „kostenlos bleiben" schließt Quiver aus. Senate eFD ist die offizielle Primärquelle – gleiche Daten wie Quiver/Capitol Trades, nur unverarbeitet. House Clerk zurückgestellt weil PTRs dort als PDF eingereicht werden (benötigt PDF-Parsing). BeautifulSoup + lxml für HTML-Parsing.

---

### [2026-04-13] Politiker-Trades Schedule → Wöchentlich (nicht täglich)

**Kontext:** Wie oft sollten Politiker-Trades abgefragt werden?

**Entscheidung:** Wöchentlich Sonntag 11:00 MEZ (nach Form 13F um 10:00).

**Begründung:** STOCK Act erlaubt Politikern bis zu 45 Tage Meldefrist. Tägliches Scraping wäre Verschwendung – die Daten ändern sich zu selten. Wöchentlich fängt neue Filings zeitnah ab, ohne unnötig Last auf die Regierungsseite zu generieren.

---

### [2026-04-13] Politiker-Trades → Kein Universe-Auto-Expand

**Kontext:** Wenn ein Politiker eine Aktie tradet, die nicht in unserem Universe ist – soll sie automatisch hinzugefügt werden?

**Entscheidung:** Nein. Trades werden gespeichert (Ticker-Feld), aber das Universe bleibt unverändert.

**Begründung:** Politiker-Trades sind ein schwaches Signal mit 30-45 Tagen Verzögerung. ARK-Holdings haben Auto-Expand verdient (klare Conviction-Signale), Politiker nicht. Würde das Universe mit Nebenwerten aufblähen.

---

### [2026-04-13] House PTRs → Auf späteren Sprint verschoben

**Kontext:** House Clerk stellt PTRs als PDF bereit, Senate eFD als HTML.

**Entscheidung:** Sprint 4 implementiert nur Senate eFD. House PTRs sind ein zukünftiges Enhancement.

**Begründung:** PDF-Parsing (OCR, tabula-py) ist signifikant komplexer als HTML-Scraping. Senate-Daten allein liefern bereits wertvolles Signal. House kann in einem zukünftigen Sprint nachgerüstet werden, wenn die Priorität dafür steht.

---

### [2026-04-13] yfinance als Fundamentals-Quelle (Sprint 5)

**Kontext:** Für Sprint 5 brauchen wir Fundamentaldaten (P/E, Margins, Revenue Growth, etc.), Analyst-Ratings und Earnings-Termine.

**Optionen:**
- A: Financial Modeling Prep (FMP) – 14 $/Monat, zuverlässige REST-API
- B: yfinance – kostenlos, bereits als Dependency vorhanden
- C: Alpha Vantage – Free Tier (25 req/Tag), zu wenig für 644 Ticker

**Entscheidung:** Option B – yfinance.

**Begründung:**
- Kostenlos (Project Constraint: „free and open-source")
- Bereits als Dependency vorhanden (seit Sprint 1)
- Liefert alle benötigten Felder: `ticker.info` (18 Metriken), `upgrades_downgrades` (Analyst-Ratings), `get_earnings_dates()` (Earnings-Kalender), `get_earnings_estimate()` (EPS Growth)
- Risiko (inoffizielle API) wird durch Graceful Error Handling mitigiert

**Revisit-Trigger:** Wenn yfinance >2 Wochen hintereinander komplett ausfällt, auf FMP migrieren.

---

### [2026-04-13] UPSERT für Fundamentals/Earnings, DO NOTHING für Ratings

**Kontext:** Fundamentals ändern sich im Tagesverlauf (z.B. nach Earnings-Calls). Earnings-Kalender-Einträge werden initial nur mit Estimates angelegt, eps_actual kommt erst post-Earnings. Analyst-Ratings sind unique Events.

**Entscheidung:**
- `FundamentalsSnapshot`: `ON CONFLICT (ticker, snapshot_date) DO UPDATE`
- `EarningsCalendar`: `ON CONFLICT (ticker, earnings_date) DO UPDATE`
- `AnalystRating`: `ON CONFLICT DO NOTHING` (Dedup via Unique Constraint)

**Begründung:** Raw-Layer ist normalerweise append-only, aber Snapshots und Kalender-Einträge sind mutable (gleicher Primärschlüssel, neuere Werte). Analyst-Ratings sind einmalige Events und brauchen nur Dedup.

---

### [2026-04-13] YFinanceClient als Shared Infrastructure

**Kontext:** Alle drei Sprint-5-Collectors (Fundamentals, Ratings, Earnings) nutzen yfinance und brauchen Rate-Limiting + Error-Handling.

**Entscheidung:** Gemeinsamer `YFinanceClient` statt Code-Duplizierung in den Collectors.

**Begründung:** Vermeidet dreifache Implementierung von Rate-Limiting (0.5s/Ticker, 3s/Batch), Batch-Iteration und Graceful Error Handling. Collectors sind schlank, YFinanceClient ist testbar.

---

### [2026-04-13] Nachtslot 01:00–03:00 MEZ für yfinance-Jobs

**Kontext:** yfinance-Abrufe für 644 Ticker dauern ~25 Min pro Collector. Bestehende Daily-Jobs laufen 22:15–00:00.

**Entscheidung:** Alle yfinance-Jobs in den Nachtslot 01:00–03:00 MEZ.

**Begründung:**
- 2-Stunden-Fenster gibt Puffer für Rate-Limit-Retries
- Yahoo Finance Servers sind nachts (19:00 ET) weniger belastet → weniger 429er
- Keine Kollision mit bestehenden Daily-Jobs (22:15–00:00) oder Weekly-Jobs (10:00–12:00)

---

### [2026-04-13] `upgrades_downgrades` statt `recommendations_summary` für Analyst-Ratings

**Kontext:** yfinance bietet zwei Analyst-Datenquellen:
- `recommendations_summary`: Aggregierte Counts (strongBuy, buy, hold, sell)
- `upgrades_downgrades`: Individuelle Firmen-Level-Einträge (Firm, ToGrade, FromGrade, Action)

**Entscheidung:** `upgrades_downgrades` verwenden.

**Begründung:** Individuelle Einträge bieten mehr Granularität: Wir wissen *welche* Firma upgraded/downgraded hat und wann. Das ist wertvoller als aggregierte Counts für spätere Feature-Berechnung (z.B. „Goldman upgraded in den letzten 7 Tagen").

---

### [2026-04-13] `eps_growth_yoy` aus `get_earnings_estimate()` statt Eigenberechnung

**Kontext:** yfinance liefert kein fertiges `eps_growth_yoy` in `ticker.info`. Wir brauchten eine Quelle.

**Optionen:**
- A: Eigenberechnung aus historischen EPS-Werten
- B: `get_earnings_estimate()['growth']` verwenden (direkt von Yahoo)

**Entscheidung:** Option B.

**Begründung:** Einfacher, weniger Code, Yahoo berechnet den Wert bereits. Eigenberechnung wäre fehleranfällig bei Split-Adjustierungen und Fiscal-Year-Unterschieden.

---

### [2026-04-13] Preis-Backfill ab 01.01.2021 via Alpaca

**Kontext:** Für aussagekräftige TA-Indikatoren (SMA 200 braucht 200 Tage), ML-Training (500k+ Samples) und Backtesting (Sprint 11) reichen wenige Tage gesammelter Daten nicht aus. Die Alpaca Free API liefert bis zu ~9 Jahre historische Preisdaten.

**Optionen:**
- A: Kein Backfill – alle Quellen starten synchron ab April 2026
- B: Preis-Backfill ab 2021, Signal-Backfill weiterhin NEIN
- C: Voller Backfill aller Datenquellen

**Entscheidung:** Option B – Preis-Backfill ab 01.01.2021 (~5,3 Jahre, ~882k Rows).

**Begründung:**
- Preise sind Basisdaten, keine Signale – kein "Pseudo-Alpha"-Risiko
- Erfasst mehrere Marktregime (COVID-Recovery, 2022 Bear, AI-Boom)
- Signal-Daten (ARK, Insider, Politiker) sind historisch nicht kostenlos verfügbar → bleiben synchron ab April 2026
- Feature Store kann NULL-Signale vor April 2026 sauber handhaben
- Edge Cases (IPOs nach 2021, Umbenennungen) werden von Alpaca transparent gehandhabt

**Revisit-Trigger:** Falls die asymmetrische Datenverfügbarkeit (Preise 5 Jahre, Signale wenige Monate) die ML-Ergebnisse nachweislich verzerrt.

---

### [2026-04-13] Relative Strength vs. SPY: Excess Return (Return-Differenz)

**Kontext:** Für den `relative_strength_spy`-Indikator musste eine Berechnungsmethode gewählt werden.

**Optionen:**
- A: Return-Ratio – `(ticker_ret / spy_ret) - 1` → Division-by-Zero-Risiko
- B: Return-Differenz (Excess Return) – `ticker_ret_20d - spy_ret_20d`
- C: Mansfield RS (Preis-Ratio, normalisiert) → zeigt Trend, nicht Betrag
- D: IBD RS-Rating (gewichtete Multi-Perioden) → zu komplex für tägliches Feature

**Entscheidung:** Option B – Return-Differenz (Excess Return).

**Begründung:**
- Kein Division-by-Zero-Risiko
- Intuitiv: +0.05 = Ticker hat SPY um 5 Prozentpunkte outperformed
- Standard in quantitativer Finanzanalyse ("Alpha")
- Lineare Skala, symmetrisch → besseres ML-Feature

---

### [2026-04-13] TA-Indikatoren-Job täglich um 22:30 MEZ

**Kontext:** Der TechnicalIndicatorsComputer muss nach dem Price Collector laufen, aber vor dem Feature Store (Sprint 7).

**Entscheidung:** 22:30 MEZ – 15 Minuten nach dem Price Collector (22:15), 30 Minuten vor ARK (23:00).

**Begründung:** Der TA-Computer hängt ausschließlich von `prices_daily` ab. Der Price Collector braucht <20s für 644 Ticker. 22:30 gibt einen konservativen Puffer ohne Kollisionsgefahr mit anderen Jobs.

---

### [2026-04-13] Sprint-Reihenfolge: Dashboard (Sprint 7) VOR Feature Pipeline (Sprint 8)

**Kontext:** Sprint 6 (TA-Indikatoren) ist abgeschlossen. Ursprüngliche Reihenfolge war Sprint 7 = Feature Pipeline, Sprint 8 = Dashboard. Sebastian möchte aber vor dem Unraid-Deployment grafisches Feedback und keine CLI-Skripte auf dem Server ausführen müssen.

**Optionen:**
- A: Feature Pipeline (Backend) → Dashboard → Deploy (original)
- B: Dashboard → Feature Pipeline → Deploy (UI sofort)
- C: Beides gleichzeitig in einem Sprint

**Entscheidung:** Option B – Dashboard wird Sprint 7, Feature Pipeline wird Sprint 8.

**Begründung:**
- Sebastian will **sofort visuelles Feedback** nach dem nächsten Sprint
- Backfill-Steuerung, DB-Bereinigung und Scheduler-Übersicht müssen im UI verfügbar sein
- Feature Pipeline (Sprint 8) wird dann automatisch ins bestehende UI integriert
- Das UI wächst mit: Jeder weitere Sprint ergänzt neue Views

---

### [2026-04-13] FastAPI + Vite/React SPA statt Streamlit

**Kontext:** ROADMAP sprach ursprünglich von „Streamlit auf Port 8501" für das Dashboard. Das Stitch-Projekt „Alpaca Scalable Broker" definiert ein anspruchsvolles Design System (Dark Mode, Cyan Primary, tonal layering, glassmorphism, no-border-rule), das in Streamlit nicht umsetzbar ist.

**Optionen:**
- A: Streamlit (einfach, schnell, aber limitiertes Design)
- B: FastAPI + Vite/React SPA (volle Design-Kontrolle, Stitch Design System 1:1 umsetzbar)
- C: Next.js (vollständig, aber eigener Node-Container nötig)

**Entscheidung:** Option B – FastAPI + Vite/React SPA.

**Begründung:**
- FastAPI ist im Projekt bereits geplant und läuft im gleichen Python-Prozess wie APScheduler
- React ermöglicht 1:1 Umsetzung des Stitch "Precision Architect" Design Systems
- Vite liefert schnellen Dev-Server und optimierte Production-Builds
- Statische SPA-Dateien werden von FastAPI als Mount-Point ausgeliefert

---

### [2026-04-13] Einzelner Container: Collector + API + UI

**Kontext:** Sollen Collector (APScheduler), API (FastAPI) und UI (React SPA) in separaten Containern oder einem einzigen laufen?

**Optionen:**
- A: 3 separate Container (Collector, API, Dashboard) – maximale Isolation
- B: 2 Container (Collector+API, Dashboard) – mittelweg
- C: 1 Container (alles zusammen) – einfachste Deployment-Topologie

**Entscheidung:** Option C – alles in einem Container auf Port 8090.

**Begründung:**
- Einfachstes Deployment auf Unraid (ein Container, ein Port)
- FastAPI startet im gleichen Python-Prozess wie APScheduler → direkter Zugriff auf Scheduler-State
- React SPA wird als statische Dateien im Docker-Build vordkompiliert und von FastAPI via `StaticFiles` mount ausgeliefert
- Dockerfile: Multi-Stage Build (Node Stage → Frontend bauen → Python Stage → Runtime)
- Kein Inter-Container-Networking nötig

---

### [2026-04-13] Echtzeit-Backfill-Fortschritt statt "Fire and Forget"

**Kontext:** Beim ersten Produktionseinsatz blieben Price- und TA-Backfills minutenlang bei 0% stehen, da der BackfillManager Fortschritt nur als Ganzes (0% → 100%) meldete.

**Optionen:**
- A: Weiterhin nur Start/Ende melden (einfach, aber schlechte UX)
- B: Per-Batch (Price) und Per-Ticker (TA) Fortschrittsupdate mit ETA-Schätzung im UI

**Entscheidung:** Option B – granularer Fortschritt mit ETA.

**Begründung:**
- Backfills dauern 5–15 Minuten – Benutzer braucht sofortiges Feedback
- ETA-Berechnung basiert auf durchschnittlicher Batch-Dauer (einfach, aber effektiv)
- Frontend pollt alle 2 Sekunden → reaktionsschnelle Anzeige
- Thread-safe updates via Lock in BackfillManager

---

### [2026-04-13] Factory Reset: Daten löschen statt DB droppen

**Kontext:** Bedarf nach einer Option, die DB komplett zu bereinigen (Werkszustand), z.B. nach fehlerhaften Imports oder zum Neustart.

**Optionen:**
- A: `DROP SCHEMA signals CASCADE` + Alembic von vorn (zerstört Schema + Universe)
- B: `TRUNCATE` aller Datentabellen (behält Schema, Universe, Alembic intakt)
- C: `DELETE FROM` aller Datentabellen (langsamer als TRUNCATE, aber transaktionssicher)

**Entscheidung:** Option C – `DELETE FROM` in einer Transaktion.

**Begründung:**
- Universe (644 Ticker) ist Basisinventar und soll erhalten bleiben
- DELETE statt TRUNCATE, weil TRUNCATE nicht in einer Transaktion rollbackbar ist
- Reihenfolge respektiert FK-Constraints (dependent tables zuerst)
- UI zeigt Bestätigungsdialog mit klarer Warnung

---

### [2026-04-13] Monatlicher Index-Sync statt manuell

**Kontext:** Die Ticker-Universe (S&P 500, Nasdaq 100) wurde einmalig per Script befüllt, aber nie automatisch aktualisiert. Bei Index-Rebalancings (ca. 4x/Jahr) würden Änderungen nicht erkannt.

**Optionen:**
- A: Manuelles Script bei Bedarf (vergisst man leicht)
- B: Wöchentlicher Sync (zu häufig, Index-Zusammensetzungen ändern sich selten)
- C: Monatlicher Sync (am 1. des Monats, deckt alle Rebalancings ab)

**Entscheidung:** Option C – monatlich am 1., 03:00 MEZ.

**Begründung:**
- Index-Rebalancings finden quartalsweise statt → monatlich reicht sicher
- Geringer API-Aufwand (2 Wikipedia-Requests + ggf. Alpaca-Validierung)
- Neue Ticker werden automatisch validiert und dem Universe hinzugefügt
- Bestandsdaten bestehender Ticker werden sofort gesammelt (nächster täglicher Lauf)
- IndexSyncer existierte bereits, musste nur als Job registriert werden

---

### [2026-04-13] SPA-Routing: Catch-All Fallback statt StaticFiles

**Kontext:** Strg+F5 (Hard Refresh) auf jeder Seite außer `/` lieferte `{"detail":"Not Found"}` vom FastAPI-Backend, weil `StaticFiles(html=True)` nur `/` → `index.html` auflöst, nicht `/universe`, `/settings` etc.

**Optionen:**
- A: `StaticFiles(html=True)` beibehalten, Benutzer muss über root navigieren (schlecht)
- B: Catch-all `@app.get("/{full_path:path}")` Route, die `index.html` für alle Nicht-API-Pfade liefert
- C: Nginx-Reverse-Proxy mit `try_files` (zusätzliche Komplexität)

**Entscheidung:** Option B – FastAPI Catch-All Route.

**Begründung:**
- Kein zusätzlicher Container/Proxy nötig
- `/assets/*` wird direkt als `StaticFiles` mount ausgeliefert (JS, CSS)
- Alle anderen Pfade liefern `index.html` → React Router übernimmt
- Statische Dateien (favicon.ico) werden direkt ausgeliefert, wenn sie existieren

---

### [2026-04-13] Live Job-Status via APScheduler Event Listener

**Kontext:** In Settings und Dashboard wurden Jobs immer als "BEREIT" / "Ausstehend" angezeigt, selbst während sie gerade liefen. Es gab keine Möglichkeit zu sehen, ob ein Job aktiv ist.

**Optionen:**
- A: Polling der `collection_log`-Tabelle (zeigt nur abgeschlossene Jobs, nicht laufende)
- B: APScheduler Event Listener (`EVENT_JOB_SUBMITTED` + `EVENT_JOB_EXECUTED/ERROR`) mit In-Memory-Tracker
- C: Redis/DB für Job-State (Overhead für 10 Jobs)

**Entscheidung:** Option B – `JobTracker` mit APScheduler Events.

**Begründung:**
- Thread-safe In-Memory-Tracker ist ausreichend für 10 Jobs
- Kein zusätzlicher Speicher/DB-Query nötig
- Events werden synchron gefeuert → sofortige Statusaktualisierung
- Dashboard pollt alle 5s, Settings alle 3s → reaktionsschnelle Anzeige
- UI zeigt "⟳ Läuft..." (gelb) + deaktiviert "Jetzt starten" Button

---

### [2026-04-13] Datenqualitäts-Kachel: UI-Feature statt DB-Tabelle

**Kontext:** Auf der TickerPage soll für jeden Ticker sichtbar sein, ob alle Daten-Dimensionen (Preise, TA-Indikatoren, Fundamentals, Signal-Updates) vollständig sind oder Lücken haben.

**Optionen:**
1. **Eigene DB-Tabelle** `data_quality_snapshots` – vorab berechnet, schnell abrufbar
2. **Live-Berechnung im API-Endpoint** – bei jedem Request aus bestehenden Tabellen berechnet
3. **Frontend-only** – separate Queries im Frontend, Client-seitig ausgewertet

**Entscheidung:** Option 2 – Live-Berechnung im Backend-Endpoint `GET /api/v1/ticker/{symbol}/data-quality`.

**Begründung:**
- Nur 4 einfache `COUNT`/`MAX`-Queries + Scheduler-Lookup → Millisekunden-Performance
- Kein Schema-Change, keine Migration, keine Data Staleness
- Ergebnis ist immer tagesaktuell
- Scheduler-Status (nächster Lauf) kann nur live abgefragt werden (nicht persistierbar)
- Frontend bekommt ein sauberes, fertig bewertetes Objekt (status: complete/partial/missing)

**Status-Schwellwerte:**
- Preise: ≥200 Tage + letzte 3 Tage aktuell → complete
- TA-Indikatoren: letzte 3 Tage aktuell → complete
- Fundamentals: Snapshot ≤14 Tage alt → complete
- Signal-Updates: Scheduler aktiv + letzter Collection-Log nicht failed → complete

**Revisit-Trigger:** Falls der Endpoint spürbar langsam wird (unwahrscheinlich bei Index-basierten COUNT/MAX-Queries).

---

### [2026-04-13] Sektor/Branche: yfinance-Enrichment statt Alpaca

**Kontext:** ~740 von 845 Universe-Tickern haben keinen Sektor – die Universe-Tabelle hat `sector`- und `industry`-Spalten, aber Alpaca's Assets-API liefert diese Daten nicht. Nur die ~100 Ticker aus dem init-Script (S&P 100) hatten manuell gesetzte Sektoren.

**Optionen:**
1. **Manuell pflegen** – unrealistisch bei 845+ Tickern
2. **yfinance `ticker.info`** – liefert `sector` + `industry` zuverlässig
3. **Drittanbieter-API** (z.B. Financial Modeling Prep) – kostet Geld

**Entscheidung:** Option 2 – yfinance Enrichment

**Begründung:**
- yfinance ist bereits Dependency (Fundamentals/Ratings/Earnings in Sprint 5)
- `ticker.info` liefert GICS-kompatible Sektor-Klassifikation
- Keine zusätzlichen Kosten oder API-Keys
- Einmal-Enrichment (~7 Minuten für ~740 Ticker) + manuell wiederholbar
- Kein Schema-Change – `sector` und `industry` Spalten existieren bereits

**Implementierung:**
- `YFinanceClient.fetch_sector_info()` – leichtgewichtige Methode (nur 2 Felder)
- `BackfillManager.start_sector_enrichment()` – Background-Thread via Settings-UI
- `POST /api/v1/ops/backfill/sectors` – API-Endpoint
- `scripts/enrich_universe_sectors.py` – CLI-Alternative

**Automatisierung:** In den monatlichen `run_index_sync()` Job integriert (1. des Monats, 03:00 MEZ). Nach dem IndexSync werden automatisch alle Ticker ohne Sektor via yfinance enriched. Zusätzlich manuell auslösbar via Settings-UI oder CLI.

---

### [2026-04-14] SEC Form 4: XSLT-Prefix stripping für primaryDocument

**Kontext:** SEC Submissions API liefert für `primaryDocument` oft XSLT-Wrapper-Pfade wie `xslF345X06/ownership.xml`. Diese Pfade existieren nicht als physische Dateien auf SEC.gov → 404.

**Entscheidung:** XSLT-Prefix `xslF345X06/` aus dem `primaryDocument`-Pfad strippen → nur `ownership.xml`.

**Begründung:** SEC rendert XSLT-Transformationen on-the-fly, speichert aber nur die rohen XML-Dateien. Der Prefix ist ein virtueller Pfad für die HTML-Ansicht, nicht für programmatischen Zugriff.

---

### [2026-04-14] SEC Form 4: Company-CIK für Archiv-URLs, nicht Filer-CIK

**Kontext:** Die Accession Number `0001178913-26-002089` enthält den Filer-CIK (`1178913`), der eine Anwaltskanzlei/Filing-Agent ist. Die tatsächlichen Dateien liegen aber unter dem Subject-Company-CIK.

**Entscheidung:** Den Company-CIK (aus `company_tickers.json`) für den Download verwenden, nicht den Filer-CIK aus der Accession Number.

**Begründung:** SEC speichert Filings unter dem Verzeichnis des Unternehmens (Subject), nicht des Filers (Agent). Beispiel: Compugen (CIK `1119774`), Filer ist Anwaltskanzlei (CIK `1178913`) → Datei liegt unter `.../data/1119774/...`.

---

### [2026-04-14] Senate eFD: curl_cffi statt Python requests

**Kontext:** Senate eFD blockiert Python `requests` mit 403 Forbidden, trotz Browser-User-Agent und vollständigen Browser-Headers. Die Seite nutzt TLS-Fingerprinting (JA3-Hash).

**Optionen:**
- A: Browser-like Headers in `requests` → 403 bleibt
- B: `curl_cffi` mit Chrome-Impersonation → TLS-Fingerprint passt
- C: `cloudscraper` → zu Cloudflare-spezifisch
- D: Headless Browser (Selenium/Playwright) → Overhead zu hoch

**Entscheidung:** Option B – `curl_cffi.requests.Session(impersonate="chrome131")`.

**Begründung:** `curl_cffi` ist ein Drop-in-Replacement für `requests` mit identischer API (Session, cookies, get/post), aber mit echtem Chrome-TLS-Fingerprint. Minimaler Code-Aufwand, keine Architekturänderung. Neue Dependency: `curl_cffi>=0.7`.

**Revisit-Trigger:** Falls Senate eFD die Bot-Detection weiter verschärft (JS-Challenge/Captcha).

---

### [2026-04-14] Senate eFD: DataTables AJAX-Endpoint statt HTML-Parsing

**Kontext:** Senate eFD liefert eine HTML-Seite mit leerer Tabelle – die Suchergebnisse werden per JavaScript/DataTables AJAX (`POST /search/report/data/`) als JSON nachgeladen. HTML-Parsing lieferte deshalb immer 0 Ergebnisse.

**Entscheidung:** Direkt den DataTables AJAX-Endpoint aufrufen statt HTML zu parsen.

**Begründung:**
- AJAX-Endpoint liefert JSON (strukturiert, robust) statt HTML (fragil)
- Server-Side Processing mit Pagination (100 Records/Seite)
- Session-Flow: 1. Agreement → 2. Search-Form POST → 3. AJAX-Daten
- Der Search-Form POST ist nötig um die Suchparameter in der Server-Session zu setzen (sonst 503)

---

### [2026-04-14] Collection-Log `notes`-Feld für Diagnose-Informationen

**Kontext:** Collector-Fehler waren ohne Container-stdout-Zugriff schwer zu debuggen. Das Collection-Log in der DB hatte nur `status`, `records_fetched` und `records_written`.

**Entscheidung:** `notes` TEXT-Spalte zum `collection_log` hinzufügen. BaseCollector speichert Diagnose-Infos (z.B. `fetch returned 1420 items`) automatisch.

**Begründung:** Macht Silent Failures sichtbar in der UI/API, ohne Container-Logs lesen zu müssen. Minimale Schema-Änderung (eine nullable TEXT-Spalte).

---

### [2026-04-15] Dividend Yield: Normalisierung wegen yfinance-Formatinkonsistenz

**Kontext:** yfinance liefert `dividendYield` in Prozent-Form (0.4 = 0.4%), während alle anderen Ratio-Felder (`profitMargins`, `operatingMargins`, `returnOnEquity`) als Dezimal kommen (0.451 = 45.1%). Unser Frontend multiplizierte alle Felder uniform mit 100 → TSM zeigte 95% statt 0.92%.

**Optionen:**
- A: Frontend-Fix: `dividend_yield` nicht *100 (inkonsistente Behandlung im Frontend)
- B: Backend-Fix: `/100` bei Speicherung (einheitliches Dezimal-Format in DB)
- C: Quellwechsel: `trailingAnnualDividendYield` statt `dividendYield` (eigene Probleme bei ADRs)

**Entscheidung:** Option B – Backend-Normalisierung + Data-Migration.

**Begründung:** Konsistente Datenhaltung. Alle Prozent-Felder liegen als Dezimal in der DB (0.0092 = 0.92%). Frontend-Code bleibt uniform (*100 für alle). Migration 013 korrigiert bestehende Daten rückwirkend.

**Revisit-Trigger:** Falls yfinance das Format erneut ändert → Plausibilitätsprüfung fängt das ab.

---

### [2026-04-15] Plausibilitätsprüfung für alle Fundamentaldaten

**Kontext:** Der Dividend-Yield-Bug zeigte, dass yfinance-Daten nicht blind vertrauenswürdig sind. Formate können sich ändern, Yahoo kann fehlerhafte Werte liefern (besonders bei ADRs).

**Entscheidung:** `_validate_fundamentals()` prüft alle 17 Felder gegen definierte plausible Ranges. Werte außerhalb → `None` + WARNING-Log.

**Begründung:**
- Defensive Programmierung: yfinance ist eine inoffizielle API, Format-Änderungen jederzeit möglich
- Lieber `None` als falsche Daten (z.B. 95% Dividendenrendite)
- WARNING-Logs machen Datenqualitätsprobleme sofort sichtbar
- Ranges bewusst großzügig (z.B. PE 0–2000, Div Yield 0–25%) um echte Extreme nicht fälschlich auszufiltern

**Ranges (Auszug):**
| Feld | Min | Max |
|------|-----|-----|
| dividend_yield | 0% | 25% |
| profit_margin | -200% | 100% |
| pe_ratio | 0 | 2000 |
| beta | -3 | 5 |

**Revisit-Trigger:** Falls regelmäßig valide Extremwerte in den Logs auftauchen → Range anpassen.

---

### [2026-04-15] Zentraler NewTickerOnboarder statt verstreuter Universe-Expansion

**Kontext:** Ticker aus Politiker-Trades (z.B. SIRI) wurden in der `politician_trades`-Tabelle gespeichert, aber nicht dem Universum hinzugefügt. Folge: Keine Preise, Indikatoren, Fundamentals. ARK hatte Universe-Expansion, aber keinen Auto-Backfill.

**Optionen:**
- A: Jeder Collector implementiert eigene `_expand_universe()` + Backfill-Logik
- B: Zentraler Service, den alle Collectors aufrufen
- C: Separater Hintergrund-Job, der periodisch nach fehlenden Tickern sucht

**Entscheidung:** Option B – Zentraler `NewTickerOnboarder` in `universe/onboarder.py`.

**Begründung:**
- DRY: Eine Stelle für Validierung + Backfill statt Duplikation in 3+ Collectors
- Konsistenz: Alle neuen Ticker durchlaufen denselben Onboarding-Prozess
- Synchron im Collector-Thread: Bei 1–5 neuen Tickern ~30–60s Overhead, akzeptabel für Nacht-Jobs
- Backfill-Pipeline: Preise (Alpaca 4J) → TA → Fundamentals → Sektor

**Form4 ausgenommen:** Der Form4-Collector ist universe-driven (sucht nur CIKs für bestehende Ticker). Neue Ticker können dort nicht entdeckt werden.

**Revisit-Trigger:** Wenn mehr als 20 neue Ticker pro Lauf auftauchen → auf asynchronen Background-Task umstellen.

---

### [2026-04-15] In-Process Log-Capture statt Docker-Log-API

**Kontext:** Beim Debugging von Collector-Problemen (z.B. Politiker-Trades, Form4) mussten Container-Logs manuell vom Unraid-Server kopiert werden. Die Logs-Seite im Dashboard zeigte nur `notes` und `errors`, aber keine Detail-Zeilen.

**Optionen:**
- A: Docker-Log-API vom Container lesen (benötigt Docker-Socket-Mount)
- B: Log-Zeilen in separater DB-Tabelle speichern (viel I/O)
- C: In-Process Logging Handler, der relevante Zeilen pro Collector-Run fängt und im `collection_log` speichert

**Entscheidung:** Option C – `CollectorLogCapture` als Context-Manager im `BaseCollector`.

**Begründung:**
- Kein Docker-Socket-Zugriff nötig (Sicherheit)
- Intelligent gefiltert: Nur WARNING+, plus collector-spezifische INFO-Zeilen
- Ring-Buffer (max 200 Zeilen) verhindert Memory-Issues
- Keine separate Tabelle, keine zusätzlichen Queries – alles in der bestehenden `collection_log.log_lines` JSONB-Spalte
- Frontend zeigt aufklappbaren Bereich mit farbcodierten Einträgen

**Revisit-Trigger:** Falls 200 Zeilen regelmäßig nicht ausreichen → `max_lines` erhöhen oder Log-Level-Filter anpassen.

---

## Noch zu treffende Entscheidungen

Alle zu Projektstart offenen Entscheidungen wurden am 2026-04-12 getroffen. Neue Entscheidungen werden hier gesammelt, sobald sie auftauchen.
