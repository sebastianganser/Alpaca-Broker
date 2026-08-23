# Anmerkungen zur Signal-Warehouse-App — Prüfliste

**Stand:** 18.08.2026 · **Grundlage:** Dashboard und Signals-Ansicht unter `192.168.1.93:8090`
**Zweck:** Punkt-für-Punkt prüfbare Liste. Jeder Eintrag hat Priorität, Aufwand und einen Haken.
**Ausführliche Begründung:** siehe `2026-08-18_Konzept_Datenerweiterung.pdf`

**Zielbild:** Die App schlägt täglich 5 Kandidaten vor und erzeugt je Kandidat eine Context-Pack-MD
mit allen Daten, die *nicht* kostenlos im Web verfügbar sind. Der Skill `aktien-analyse` liest
diese Datei und ergänzt sie um qualitative Web-Recherche.

---

## Legende

| Symbol | Bedeutung |
|---|---|
| 🔴 | Blocker — ohne das ist der Rest wenig wert |
| 🟠 | Hoher Ertrag |
| 🟡 | Mittlerer Ertrag |
| ⚪ | Nice to have |
| ⏱ | grobe Aufwandsschätzung |

**Annahmen, die ich getroffen habe** (bitte korrigieren, falls falsch): PostgreSQL als Datenbank,
SQLAlchemy + Alembic, Python-Collectoren mit gemeinsamem Basis-Interface, APScheduler o. ä.
für das Job-Scheduling.

---

## A. Kritische Befunde — vor allem anderen klären

### A1 🔴 Survivorship Bias im Universum ⏱ 2–4 Tage

- [ ] **Prüfen:** Sind die 749 Titel in `universe` die *heutigen* S&P-500- und Nasdaq-100-Mitglieder?
- [ ] **Prüfen:** Enthält `prices_daily` auch Titel, die seit 2020 aus dem Index geflogen oder
      delistet sind?
- [ ] `index_membership` historisch rekonstruieren (S&P-500-Änderungshistorie mit Datum ist
      öffentlich dokumentiert und einmalig parsebar)
- [ ] Delistete Ticker in `prices_daily` nachladen — Alpaca liefert über den Assets-Endpunkt
      auch inaktive Symbole, `asof`-Parameter für historisches Symbol-Mapping
- [ ] Feature-Berechnung auf das **zum jeweiligen Stichtag gültige** Universum umstellen

> Wenn das Universum die heutige Indexzusammensetzung ist, weiß das Modell beim Training auf
> 2021er-Daten bereits, welche Firmen überlebt haben. Der Backtest zeigt dann Alpha, das im
> Livebetrieb nicht existiert. Das ist kein Randproblem — es entwertet potenziell die gesamte
> Modellvalidierung.

### A2 🔴 Lookahead-Prüfung über alle Collectoren ⏱ 1–2 Tage

- [ ] Für jede Tabelle dokumentieren: Welches Datum ist der **Zeitpunkt der Verfügbarkeit**?
- [ ] `insider_trades` — Feature muss auf `filing_date` beruhen, nicht auf `transaction_date`
- [ ] `politician_trades` — auf `disclosure_date`, nicht auf `trade_date` (siehe C1)
- [ ] `form13f_holdings` — Quartalsende + ~45 Tage Verzögerung, nicht am Quartalsende verfügbar
- [ ] `fundamentals_snapshot` — läuft bereits point-in-time ab 16.04.2026, gut
- [ ] `earnings_calendar` — reicht bis 03/2027, also Zukunftsdaten. Beim Backtest darf nur
      genutzt werden, was zum Stichtag bereits angekündigt war
- [ ] Einheitliche Spalte `available_from` in allen Signal-Tabellen ergänzen

---

## B. Fehlende Datenquellen — Priorität 1 (kostenlos, Historie rückwirkend)

### B1 🟠 EPS-/Umsatz-Konsens und Revisionszähler ⏱ 0,5 Tage — **zeitkritisch**

- [ ] Collector `estimates_snapshot` bauen, täglich
- [ ] Quelle: `yfinance` → `eps_trend`, `eps_revisions`, `earnings_estimate`, `revenue_estimate`
- [ ] Liefert **90 Tage Rückwirkung** — also sofort Historie bis Mitte Mai
- [ ] Felder: avg/low/high, Analystenzahl, `7daysAgo`/`30daysAgo`/`60daysAgo`/`90daysAgo`,
      `upLast7days`/`upLast30days`/`downLast7days`/`downLast30days`
- [ ] Abgeleitetes Feature: **Revisions-Momentum** = (up − down) / (up + down) über 30 Tage

> **Zeitkritisch:** Das 90-Tage-Fenster bei Yahoo rollt. Jeder Tag Verzögerung kostet einen Tag
> Historie, der unwiederbringlich ist. Das ist der einzige Punkt auf dieser Liste, der nicht warten
> kann.
>
> Vorbehalt: `yfinance` ist eine inoffizielle Schnittstelle, gegen Yahoos ToS für kommerzielle
> Nutzung, ohne SLA. Für ein privates Tool vertretbar. Lizenzierte Alternative mit identischem
> Datenmodell: EODHD `calendar/trends`, ca. 50 $/Monat im Jahresabo.

### B2 🟠 Earnings Surprise / SUE ⏱ 0,5 Tage

- [ ] `earnings_calendar` um **Ist-Werte** erweitern: tatsächlicher EPS und Umsatz vs. Schätzung
- [ ] Standardized Unexpected Earnings berechnen: (Ist − Erwartung) / Standardabweichung
      der Schätzungen
- [ ] Quelle: `yfinance` → `earnings_history` (liefert mehrere Jahre rückwirkend)
- [ ] Feature: Tage seit letztem Earnings + SUE → Post-Earnings-Announcement-Drift

> PEAD ist eine der am längsten dokumentierten Anomalien überhaupt. Du hast den Kalender,
> aber offenbar nicht die Überraschungskomponente.

### B3 🟠 Benchmark- und Sektor-Zeitreihen ⏱ 0,5 Tage

- [ ] SPY, QQQ, IWM und die elf GICS-Sektor-ETFs (XLK, XLF, XLV, XLE, XLY, XLP, XLI, XLB,
      XLU, XLRE, XLC) in `prices_daily` aufnehmen
- [ ] Quelle: Alpaca, Historie ab 2016
- [ ] Feature: **relative Stärke** über 1/3/6/12 Monate gegen Index **und** Sektor

> Ohne Benchmark ist keine relative Stärke berechenbar. Die relative Performance in einem
> steigenden Markt ist trennschärfer als fast jeder absolute technische Indikator.

### B4 🟠 Sektor- und Branchenklassifikation ⏱ 0,5 Tage

- [ ] Spalten `sector`, `industry`, `market_cap_bucket` in `universe`
- [ ] Quelle: `yfinance` → `info`, oder FMP
- [ ] Beim Modelltraining sektor-neutralisieren oder Sektor als Kategorial-Feature führen

> Ohne Sektorzugehörigkeit lernt das Modell Branchenrotation und weist sie als Feature-Importance
> aus. Ein erheblicher Teil der gemessenen Trennschärfe ist dann kein Titel-Alpha.

### B5 🟠 Short Interest ⏱ 0,5 Tage

- [ ] Tabelle `short_interest`, zweimal monatlich
- [ ] Quelle: Massive (ehemals Polygon), Free-Tier enthält **2 Jahre Historie**
- [ ] Alternativ/ergänzend: FINRA Developer Center, Public Credentials kostenlos
- [ ] Felder: Short Interest, Days to Cover, Short % of Float, Settlement Date
- [ ] Feature: Niveau **und** Veränderung gegenüber der Vorperiode

---

## C. Datenqualität in bestehenden Tabellen

### C1 🟠 Politiker-Trades: Lag-Gewichtung ⏱ 0,5 Tage

- [ ] Trades mit `disclosure_lag > 45 Tage` als Feature abwerten oder ausschließen
- [ ] Beobachtet: Tuberville meldet 2024er-Trades mit **824 Tagen** Verzögerung,
      Wyden mit **465 Tagen**
- [ ] Feature-Variante: exponentielle Abwertung nach Lag statt harter Filter

> Die `VERZÖG.`-Spalte existiert bereits — sie wird nur offenbar noch nicht als Gewicht genutzt.
> Ein Trade mit über zwei Jahren Meldeverzug hat keinerlei Informationswert, verwässert aber
> jedes Aggregat.

### C2 🟡 Politiker-Trades: leere Partei-Spalte ⏱ 0,5 Tage

- [ ] Partei-Zuordnung nachziehen (Mapping-Tabelle Politikername → Partei/Kammer/Ausschuss)
- [ ] Interessant als Feature: Ausschusszugehörigkeit vs. gehandelter Sektor

### C3 🟡 Ticker-Parser ⏱ 0,25 Tage

- [ ] `--AMCR` in der Politiker-Tabelle deutet auf einen Parser-Fehler hin
- [ ] Validierung gegen `universe` beim Insert, ungültige Symbole in Quarantäne-Tabelle
      statt still verwerfen

### C4 🟡 Kursbereinigung prüfen ⏱ 0,25 Tage

- [ ] Wird `prices_daily` mit `adjustment=all` von Alpaca geholt (Splits **und** Dividenden)?
- [ ] Falls nur `raw` oder `split`: Forward Returns sind systematisch verzerrt

---

## D. Fehlende Datenquellen — Priorität 2

### D1 🟠 Makro-Regime über FRED ⏱ 0,5 Tage

- [ ] Tabelle `macro_series`, täglich
- [ ] Quelle: FRED-API, kostenlos, Historie über Jahrzehnte **sofort rückwirkend**
- [ ] Serien: `DGS2`, `DGS10` (plus Spread 10y−2y), `BAMLH0A0HYM2` (High-Yield-Spread),
      `VIXCLS`, `DTWEXBGS` (Dollar-Index), `T10YIE` (Inflationserwartung)
- [ ] Abgeleitet: Regime-Klassifikation (Risk-on / Risk-off / Übergang)

> Derselbe Faktor wirkt in unterschiedlichen Marktphasen unterschiedlich. Ohne Regime-Kontext
> mittelt das Modell über Phasen hinweg, in denen ein Signal funktioniert, und solche, in denen
> es das Gegenteil tut.

### D2 🟡 Marktbreite ⏱ 0,25 Tage

- [ ] Aus vorhandenen Daten berechenbar, keine neue Quelle
- [ ] Anteil der Titel über 50-/200-Tage-Linie, Advance/Decline-Ratio,
      Anteil auf 52-Wochen-Hoch/-Tief

### D3 🟡 Optionsdaten und implizite Volatilität ⏱ 1–2 Tage

- [ ] Tabelle `options_iv_snapshot`, täglich
- [ ] Quelle: **Alpaca, im vorhandenen kostenlosen Zugang enthalten** — Optionskette liefert
      Delta/Gamma/Theta/Vega/Rho und implizite Volatilität. Historie ab Februar 2024
- [ ] Open Interest separat aus dem Trading-Endpunkt `/v2/options/contracts`
- [ ] Features: ATM-IV, **IV-Rank und IV-Perzentil**, Put/Call-Skew, IV-Terminstruktur,
      IV/RV-Verhältnis
- [ ] Brücke: realisierte Volatilität sofort aus `prices_daily` rechnen; IV-Rank braucht
      ein Jahr Vorlauf

---

## E. Aus vorhandenen Daten ableitbar — bester Ertrag pro Aufwand

### E1 🟠 13F-Deltas ⏱ 0,5 Tage

- [ ] Analog zu `ark_deltas` auch für `form13f_holdings` berechnen
- [ ] Features: Positionsveränderung je Fonds, **Anzahl haltender Fonds** und deren Veränderung,
      Konzentration (Anteil der Top-10-Halter)

> Der Bestand ist statisch, die Veränderung ist das Signal. Für ARK machst du das bereits —
> für die Institutionellen offenbar noch nicht.

### E2 🟡 Insider-Ratio als kontinuierliches Feature ⏱ 0,25 Tage

- [ ] Kauf/Verkauf-Verhältnis je Titel über 30/90/180 Tage
- [ ] Nach Volumen gewichtet, nicht nach Anzahl der Transaktionen
- [ ] Käufe getrennt von planmäßigen 10b5-1-Verkäufen behandeln
- [ ] `insider_clusters` existiert als Flag — die kontinuierliche Ratio ist meist trennschärfer

### E3 🟡 Sentiment-Momentum ⏱ 0,25 Tage

- [ ] Nicht nur den FinBERT-Score speichern, sondern dessen 7-/30-Tage-Veränderung
- [ ] Zusätzlich: Nachrichtenvolumen und dessen Abweichung vom Titel-Durchschnitt

> Bei Sentiment ist die Änderungsrate fast immer aussagekräftiger als das Niveau.

### E4 🟡 Liquiditätsmaße ⏱ 0,25 Tage

- [ ] Dollar-Volumen (20-Tage-Durchschnitt), Amihud-Illiquidität
- [ ] Dient doppelt: als Feature **und** als Handelbarkeits-Filter in der Kandidatenauswahl

---

## F. Kandidaten-Pipeline (das eigentliche Ziel)

### F1 🔴 Ranking und Auswahl ⏱ 2–3 Tage

- [ ] Tabelle `candidate_selections` — täglicher Lauf, append-only
- [ ] Cross-sectionales Ranking über `feature_snapshots`
- [ ] **Guardrails**, ohne die die Auswahl unbrauchbar wird:
  - [ ] Mindest-Liquidität (z. B. 20-Tage-Dollar-Volumen > 20 Mio. USD)
  - [ ] Maximal 2 Kandidaten je Sektor
  - [ ] Paarkorrelation der Kandidaten unter einem Schwellwert
  - [ ] Kein Titel, der in den letzten N Tagen bereits vorgeschlagen wurde
  - [ ] **Mindest-Score:** Wenn weniger als 5 Titel die Schwelle reißen, weniger vorschlagen.
        „Heute keine Kandidaten" ist ein gültiges Ergebnis
  - [ ] Earnings innerhalb der nächsten 5 Handelstage: markieren, nicht ausschließen
- [ ] Jede Auswahl mit **Feature-Attribution** speichern (welche Features haben zum Score
      beigetragen) — sonst ist der Vorschlag eine Blackbox

### F2 🔴 Context-Pack-Generator ⏱ 2–3 Tage

- [ ] Je Kandidat eine MD-Datei erzeugen, die **genau das** enthält, was online nicht frei ist
- [ ] Vollständige Struktur: siehe Konzept-PDF, Kapitel 6
- [ ] Kern der sechs Blöcke:
  1. **Cross-sectionale Perzentile** — „KGV 35" sagt wenig, „KGV im 92. Perzentil des
     Universums" sagt viel
  2. **Eigene Point-in-Time-Historie** — wie haben sich Estimates, Ratings und Fundamentals
     seit März/April bewegt
  3. **Signal-Stack** — ARK-Deltas, Insider-Ratio, Politiker (lag-gefiltert), 13F-Deltas,
     jeweils mit korrektem Verfügbarkeitsdatum
  4. **Modell-Attribution** — warum dieser Titel, welche Features haben getrieben
  5. **Historisches Analogon** — als dieselbe Feature-Konstellation historisch auftrat: welche
     Forward Returns über 20/60 Tage, mit welcher Trefferquote und Stichprobengröße
  6. **Peer-Set aus dem echten Universum** berechnet, nicht handverlesen
- [ ] YAML-Frontmatter mit maschinenlesbaren Kennzahlen, damit der Skill zuverlässig parsen kann

> Block 5 ist der eigentliche Mehrwert. Du hast Forward-Return-Targets — damit kannst du eine
> Frage beantworten, die keine Web-Recherche beantworten kann: „Was ist historisch passiert,
> wenn es so aussah wie heute?" Genau das ist die belastbare Form der abweichenden Markterwartung,
> auf der jede Trade-These aufbauen sollte.

### F3 🟠 Skill-Anpassung ⏱ 1 Tag

- [ ] `aktien-analyse` um einen zweiten Modus erweitern: „Context Pack vorhanden"
- [ ] Ablauf dann: Context Pack lesen → nur noch die qualitativen Lücken per Web recherchieren
      (Narrativ, Guidance im Wortlaut, Regulierung, Katalysatoren) → zusammenführen
- [ ] Arbeitsteilung sauber trennen:
  - **App liefert:** Zahlen, Perzentile, eigene Historie, Modell-Sicht, historische Analogie
  - **Web liefert:** Narrativ, Guidance-Wortlaut, Rechtslage, Termine, Analystenbegründungen
- [ ] Die Entscheidungslogik bleibt unverändert: CRV-Schwelle, Invalidierungspunkt,
      „Neutral ist ein gültiges Ergebnis"

---

## G. Methodische Fallstricke — beim Modellieren beachten

- [ ] **Multiple Testing:** Bei 50+ Features findet man immer etwas. Out-of-Sample-Zeitraum
      strikt getrennt halten, Walk-Forward statt einfachem Train/Test-Split
- [ ] **Transaktionskosten und Slippage** im Backtest berücksichtigen — sonst überschätzt man
      besonders bei kurzen Horizonten massiv
- [ ] **Regime-Overfitting:** 2020–2026 enthält eine sehr spezielle Marktphase. Ein Modell,
      das nur diese kennt, ist nicht robust
- [ ] **Feature-Stabilität:** Die monatliche Importance-Analyse läuft bereits — zusätzlich
      prüfen, ob die Rangfolge über die Zeit stabil bleibt oder springt
- [ ] **Datenlecks über Korrelation:** `technical_indicators` und `prices_daily` teilen dieselbe
      Information mehrfach. Multikollinearität verzerrt Importance-Maße

---

## H. Was ich bewusst nicht empfehle

| Was | Warum nicht |
|---|---|
| Dark-Pool-Daten | Ab ca. 150 $/Monat, Vorhersagehorizont Stunden bis Tage — passt nicht zu einem Tagesmodell |
| Fertiger Options-Flow | Gleiches Kostenargument; Rohdaten liegen bei Alpaca im Free-Tier |
| Intraday-Tickdaten | Enormer Speicherbedarf, kein Nutzen für Forward Returns über 20+ Tage |
| Zacks/Refinitiv-Konsens | Nur Enterprise-Lizenz, Preise nicht öffentlich, ab vierstellig monatlich |

Order Flow lässt sich später aus Alpaca-Ticks selbst rechnen (Lee-Ready-Klassifikation über
Trades + NBBO-Quotes). Erst sinnvoll, wenn kürzere Horizonte dazukommen.

---

## Vorgeschlagene Reihenfolge

| # | Punkt | Warum an dieser Stelle |
|---|---|---|
| 1 | **B1** Estimates-Collector | Zeitkritisch — rollierendes 90-Tage-Fenster |
| 2 | **A1** Survivorship Bias | Blocker für jede Modellvalidierung |
| 3 | **A2** Lookahead-Prüfung | Gleicher Grund, geringerer Aufwand |
| 4 | **D1** FRED-Regime | Einmal gebaut, sofort volle Historie |
| 5 | **B3 + B4** Benchmarks und Sektoren | Voraussetzung für relative Stärke |
| 6 | **B2 + B5** SUE und Short Interest | Beide rückwirkend verfügbar |
| 7 | **E1–E4** abgeleitete Features | Kein neuer Collector nötig |
| 8 | **C1–C4** Datenqualität | Parallel möglich, kleine Brocken |
| 9 | **D3** Optionen/IV | IV-Rank braucht Vorlauf, also früh starten trotz später Nutzung |
| 10 | **F1–F3** Kandidaten-Pipeline | Erst wenn die Datenbasis steht |

---

## Offene Fragen an dich

1. Ist `universe` das heutige oder ein historisch korrektes Index-Universum? (entscheidet über A1)
2. Enthält `analyst_ratings` nur Ratings und Kursziele, oder auch EPS-Konsens? (entscheidet, ob B1
   eine Lücke oder eine Ergänzung ist)
3. Läuft `prices_daily` mit `adjustment=all`?
4. PostgreSQL? Gemeinsames Basis-Interface für Collectoren?
5. Welcher Modelltyp erzeugt aktuell die Feature-Importance — Baum-Verfahren, lineares Modell,
   oder noch reine Korrelationsanalyse?
6. Soll das Context Pack pro Kandidat eine Datei sein, oder ein Verzeichnis je Handelstag mit
   fünf Dateien plus einer Übersicht?

---

*Erstellt am 18.08.2026 · Keine Anlageberatung · Aufwandsschätzungen sind grob und
setzen Vertrautheit mit der eigenen Codebasis voraus*
