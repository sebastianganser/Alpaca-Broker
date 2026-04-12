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

1. **Politiker-Trades-Quelle → Quiver Quantitative API** (statt Capitol Trades Scraping)
   Begründung: Offizielle API, saubere JSON-Responses, rechtlich unbedenklich, Free Tier verfügbar. Scraping wäre fragil und in der ToS-Grauzone. Da Politiker-Signale ohnehin als schwach eingeschätzt werden, lohnt sich keine fragile Infrastruktur.

2. **TA-Library → pandas-ta** (statt ta-lib)
   Begründung: Reines Python, installiert sich ohne C-Compiler-Drama. Performance ist für EOD-Daten völlig ausreichend. Robustheit schlägt Geschwindigkeit bei täglichen Berechnungen auf ~600 Titeln.

3. **Paket-Manager → uv** (statt Poetry)
   Begründung: 10–100x schneller als Poetry, verwaltet Python-Versionen mit, kompatibel mit Standard-pyproject.toml. Astral hat sich 2024/2025 als neuer Standard etabliert. Kein Legacy-Grund für Poetry bei einem Neustart.

4. **Scheduler → APScheduler** (statt Cron)
   Begründung: Ein langlebiger Python-Container, gemeinsames Logging und Config, Job-Dependencies modellierbar, dynamische Steuerung aus dem Code. Cron würde Kontext und Error-Handling fragmentieren.

5. **Monitoring → Telegram + E-Mail-Fallback**
   Begründung: Telegram-Bot für Push-Nachrichten (Daily Summary, akute Warnungen), E-Mail als zweiter Kanal für kritische Fehler und archivierbare Reports. Python-telegram-bot ist trivial einzurichten.

6. **Dashboard → Streamlit** (statt statisches HTML)
   Begründung: Interaktive Plots, Filter, Auto-Refresh in 50 Zeilen Python. Läuft als Docker-Container auf Port 8501. Keine Build-Pipeline, keine React-Lernkurve. Perfekt für explorative Analyse.

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
- Punkt 1: Falls Quiver Free Tier nicht ausreicht oder Daten unzuverlässig sind
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

## Noch zu treffende Entscheidungen

Alle zu Projektstart offenen Entscheidungen wurden am 2026-04-12 getroffen. Neue Entscheidungen werden hier gesammelt, sobald sie auftauchen.
