# LEARNINGS.md – Erkenntnisse aus den Daten

> Lebendes Dokument. Wächst mit, während wir Daten sammeln und auswerten.
> Hier landen alle Beobachtungen, Aha-Momente, gescheiterte Hypothesen und bestätigte Muster.

---

## Wie dieses Dokument genutzt wird

Jeder Eintrag folgt einem einfachen Schema:

```markdown
### [YYYY-MM-DD] Titel der Erkenntnis

**Beobachtung:** Was haben wir gesehen?
**Daten:** Auf welcher Datenbasis?
**Hypothese:** Was könnte dahinter stecken?
**Nächste Schritte:** Wie verifizieren oder widerlegen wir das?
**Status:** 🟡 Offen / 🟢 Bestätigt / 🔴 Widerlegt
```

---

## Kategorien von Erkenntnissen

Wir tracken Erkenntnisse in mehreren Kategorien:

- **📊 Datenqualität** – Welche Quellen sind wie zuverlässig?
- **🎯 Signalstärke** – Welche Features korrelieren mit zukünftiger Performance?
- **🔬 Muster** – Wiederkehrende Phänomene in den Daten
- **⚠️ Fallen** – Dinge, die irreführend aussehen, aber kein echtes Signal sind
- **💡 Hypothesen** – Neue Ideen, die wir testen könnten
- **🛠️ Technisches** – Lessons Learned bei Implementierung und Betrieb

---

## Erkenntnisse

### [2026-04-15] 📊 Erste Politiker-Trade-Daten: Auffällige Aktivität einzelner Senatoren

**Beobachtung:** Beim ersten erfolgreichen Senate eFD Import (161 PTR-Filings, 636 Transaktionen) fällt auf, dass einige wenige Senatoren extrem aktiv handeln. John Boozman hat allein am 14.04.2026 mindestens 10 Transaktionen gemeldet (diverse Purchases + Sales: VLA, SPYN, NVDA, MSFT, ARES, FIGC, TPVP, TNC, BREU). John Fetterman zeigt ähnliches Muster mit 8+ Trades am 03.04.2026 (MSFT, ERIE, AMZN, GOOG, FRTF, NU).

**Daten:** 636 Politiker-Trades aus 161 PTR-Filings (12 Monate Lookback).

**Hypothese:** Diese Senatoren diversifizieren aktiv ihre Portfolios. Die hohe Transaktionsfrequenz bei kleinen Beträgen ($1,001-$15,000) deutet auf regelmäßiges Rebalancing hin – wahrscheinlich **kein starkes Alpha-Signal** für Einzeltrades. Interessanter wären große Einzeltrades (>$50,000), wie Tina Smiths MMM-Sale ($50,001-$100,000) und BRK.B-Sale ($100,001-$250,000).

**Nächste Schritte:** Nach 1 Monat: Kategorisierung nach Betragshöhe, Frequenz-Analyse pro Senator.

**Status:** 🟡 Offen

---

### [2026-04-15] 📊 yfinance-Formatinkonsistenz: dividendYield in anderer Skala als andere Ratio-Felder

**Beobachtung:** TSM zeigte 95% Dividendenrendite im Dashboard. Ursache: yfinance liefert `dividendYield` in Prozent-Form (0.92 = 0.92%), während `profitMargins`, `operatingMargins` etc. als Dezimal kommen (0.451 = 45.1%). Unser Code behandelte alle Felder gleich (*100 im Frontend).

**Daten:** Systematisch über 6 Ticker verifiziert (AAPL=0.4, MSFT=0.93, TSM=0.92, JNJ=2.19, GOOG=0.25 – alle bereits Prozentwerte). Alle anderen Ratio-Felder konsistent als Dezimal.

**Hypothese:** Yahoo Finance API liefert verschiedene Felder in unterschiedlichen Skalen. Da yfinance eine inoffizielle Wrapper-Bibliothek ist, kann sich das Format jederzeit ändern. → **Defensive Programmierung mit Plausibilitätsprüfung ist Pflicht.**

**Nächste Schritte:** Monitoring: WARNING-Logs bei Plausibilitätsverletzungen regelmäßig prüfen.

**Status:** 🟢 Bestätigt & behoben (Migration 013 + Plausibilitätsprüfung)

---

### [2026-04-15] 📊 Architektur-Lücke: Ticker ohne Universe-Eintrag haben keine Daten

**Beobachtung:** SIRI (über Politiker-Trade von Hickenlooper) war in der `politician_trades`-Tabelle gespeichert, aber im Dashboard fehlten Preise, Indikatoren und Fundamentals komplett. Grund: Der `politician_trades_collector` fügte Trades nur in seine eigene Tabelle ein, aber **nicht** ins Universum. Alle anderen Collectors (Preise, TA, Fundamentals) lesen nur Ticker aus der `universe`-Tabelle (`WHERE is_active = true`).

**Root Cause:** Inkonsistente Architektur – der ARK-Collector hatte `_expand_universe()`, der Politiker-Collector nicht. Form4 war korrekt (universe-driven).

**Hypothese:** Jeder Collector, der Ticker aus externen Quellen discovert (nicht nur bestehende Ticker abfragt), muss die neuen Ticker dem Universum hinzufügen. → Zentraler Service statt verstreuter Logik.

**Lösung:** `NewTickerOnboarder` (`universe/onboarder.py`) – Alpaca-Validierung + automatischer Backfill-Pipeline (Preise → TA → Fundamentals → Sektor). Wird jetzt von Politiker- und ARK-Collector aufgerufen.

**Status:** 🟢 Behoben (Session 14)

---

## Geplante Untersuchungen

Sobald genug Daten vorliegen, wollen wir diese Fragen systematisch untersuchen:

### Phase 1 – Nach 1 Monat Datensammlung

- [ ] **Datenqualität:** Wie viele Handelstage haben wir lückenlos? Wie oft sind Quellen ausgefallen?
- [ ] **Universum-Wachstum:** Wie schnell wächst das Titel-Universum? Welche Quellen fügen am meisten Titel hinzu?
- [ ] **ARK-Aktivität:** Wie oft ändern sich ARK-Holdings? Sind die meisten Deltas Rebalances oder echte Conviction?
- [ ] **Insider-Frequenz:** Wie viele Form-4-Filings pro Tag? Welche Unternehmen haben die meisten Insider-Trades?

### Phase 2 – Nach 3 Monaten Datensammlung

- [ ] **Korrelationen Feature ↔ Returns:** Welche Features korrelieren mit 1/5/20-Tages-Returns?
- [ ] **Cluster-Analyse:** Wie oft gibt es Insider-Cluster? Wie korrelieren sie mit Kursbewegungen?
- [ ] **ARK-Predictive-Power:** Wenn ARK nachkauft, wie entwickelt sich der Titel in den nächsten 20 Tagen?
- [ ] **Politiker-Delay:** Ist das Signal wirklich so verzögert wie befürchtet?
- [ ] **Multi-Signal-Überlappungen:** Wie oft stimmen zwei oder drei Signalquellen bei demselben Titel überein?

### Phase 3 – Nach 6 Monaten Datensammlung

- [ ] **Feature-Importance:** Welche Features sind laut Random Forest am wichtigsten?
- [ ] **Optimales Scoring:** Welche Gewichtungen liefern die besten Backtests?
- [ ] **Sektor-Unterschiede:** Funktionieren Signale in allen Sektoren gleich gut?
- [ ] **Zeitliche Stabilität:** Sind Signale über Marktphasen (Bullen/Bären) stabil?
- [ ] **False-Positive-Rate:** Wie viele "gute Scores" führten zu Verlusten?

---

## Hypothesen (zu testen)

Liste von Hypothesen, die wir in den Daten verifizieren wollen:

### H1: ARK-Käufe in mehreren ETFs parallel sind starkes Signal
Wenn Cathie Woods' Team denselben Titel in mehreren ARK-ETFs aufstockt, ist das eher Conviction als Rebalancing.

### H2: Insider-Cluster schlagen einzelne Insider-Käufe
Wenn mehrere Insider eines Unternehmens innerhalb weniger Tage kaufen, ist das prädiktiver als ein einzelner großer Kauf.

### H3: Form 4 in Kombination mit ARK ist stärker als jedes einzeln
Signale aus verschiedenen Quellen sollten unabhängig sein und sich gegenseitig verstärken.

### H4: Gewichtungsänderungen in ARK sind aussagekräftiger als absolute Shares
Weil Share-Änderungen auch von In-/Outflows getrieben sein können, sind Weight-Änderungen das reinere Signal.

### H5: ARK-Verkäufe sind schlechter prädiktiv als ARK-Käufe
Verkäufe können viele Gründe haben (Rebalancing, Outflows), Käufe sind gezielter.

### H6: Kleine Titel reagieren stärker auf ARK-Trades als große
Market-Impact-Effekt: ARK bewegt bei Small Caps den Preis mit.

### H7: Politiker-Trades sind zu verzögert für Alpha
Aber vielleicht als Feature in einer Kombination trotzdem hilfreich.

### H8: Technische Indikatoren alleine erzeugen keine Alpha-Signale
Aber in Kombination mit Fundamentaldaten könnten sie einen Beitrag leisten.

### H9: Analyst-Downgrades sind stärkere Signale als Upgrades
Weil Banken selten negativ über ihre Kunden schreiben – wenn sie es tun, ist es ernst.

### H10: Cluster-Käufe von Insidern nach einem Earnings-Drop sind ein Konstruktionsdach-Signal
Insider kaufen, wenn der Markt überreagiert hat.

---

## Gescheiterte Ansätze (für später)

Hier landen Strategien, die wir ausprobiert haben und die sich als nicht funktionsfähig erwiesen. Genauso wichtig wie bestätigte Signale, um Wiederholungen zu vermeiden.

*(Noch leer)*

---

## Meta-Learnings zum Projekt selbst

### [2026-04-15] 🛠️ TLS-Fingerprinting wird zum Standard bei Government-Seiten

Die Senate eFD Seite blockiert Python `requests` nicht über User-Agent oder Header-Analyse, sondern über **TLS-Fingerprinting (JA3-Hash)**. Das bedeutet: egal welche Headers wir senden, die TLS-Handshake-Signatur verrät, dass kein echter Browser verbindet. Lösung: `curl_cffi` mit Chrome-Impersonation. Erwartung: Weitere Government- und Finanz-Seiten werden ähnliche Bot-Detection nutzen.

### [2026-04-15] 🛠️ DataTables Server-Side Processing erfordert Session-Kontext

Die Senate eFD Seite rendert keine HTML-Tabellen mehr Server-seitig. Stattdessen: leeres HTML-Template + JavaScript/AJAX-Datenabruf (`/search/report/data/`). Der AJAX-Endpoint braucht aber einen vorherigen Search-Form POST, um die Suchparameter in der Server-Session zu speichern (sonst 503). Lesson: Bei Scraping immer erst den vollständigen Browser-Flow nachbilden.

### [2026-04-15] 🛠️ SEC archiviert unter Subject-CIK, nicht Filer-CIK

Die Accession Number eines SEC Filing enthält den CIK des **Filers** (oft eine Anwaltskanzlei oder Filing-Agent), aber die Dateien liegen im Archiv unter dem CIK des **Subject Company** (also des Unternehmens). Lesson: Bei SEC-Downloads immer den Company-CIK aus `company_tickers.json` verwenden, nicht den CIK aus der Accession Number.
