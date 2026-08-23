# Signal Warehouse — Konzept zur Datenerweiterung und Kandidaten-Pipeline

**Stand:** 18. August 2026
**Gegenstand:** Erweiterung der bestehenden Signal-Warehouse-App um fehlende Datendimensionen,
Aufbau einer täglichen Kandidatenauswahl und Definition der Schnittstelle zum Analyse-Skill
**Grundlage:** Dashboard- und Signals-Ansicht unter `192.168.1.93:8090`, Stand 18.08.2026
**Begleitdokument:** `2026-08-18_Anmerkungen_Datenerweiterung.md` (Prüfliste zum Abarbeiten)

> **Hinweis:** Dieses Dokument beschreibt technische Architektur und Datenquellen. Es ist keine
> Anlageberatung und keine Handelsempfehlung. Alle Aussagen über die Wirksamkeit von Faktoren
> sind Hinweise auf die Literatur und auf allgemeine Marktmechanik, keine Renditeversprechen.

---

## 1. Ausgangslage

Was ich beim Blick auf das Dashboard gesehen habe, ist keine Sammlung von Skripten, sondern eine
funktionierende Feature-Pipeline mit Zielvariablen. Das ist ein wesentlich weiter fortgeschrittener
Stand als das, was man üblicherweise unter „Aktien-Tracker" versteht.

### Bestand zum 18.08.2026

| Tabelle | Einträge | Zeitraum | Charakter |
|---|---|---|---|
| `prices_daily` | 975,5k | 02.11.2020 – 18.08.2026 | Marktdaten |
| `technical_indicators` | 974,5k | 03.11.2020 – 18.08.2026 | abgeleitet |
| `insider_trades` | 360,8k | 01.01.2021 – 18.08.2026 | Signal |
| `form13f_holdings` | 65,4k | 31.12.2025 – 30.06.2026 | Signal |
| `feature_snapshots` | 69,0k | 12.05.2026 – 18.08.2026 | Feature-Store |
| `ark_holdings` / `ark_deltas` | 27,6k / 10,4k | ab 15.04.2026 | Signal |
| `earnings_calendar` | 18,9k | 29.06.1999 – 01.03.2027 | Ereignis |
| `fundamentals_snapshot` | 13,1k | ab 16.04.2026 | Point-in-Time |
| `analyst_ratings` | 12,8k | ab 17.03.2026 | Point-in-Time |
| `politician_trades` | 1,2k | 20.04.2025 – 08.08.2026 | Signal |
| `insider_clusters` | 401 | ab 10.01.2023 | abgeleitet |
| `universe` | 749 | — | Stammdaten |

Dazu 16 aktive Jobs, FinBERT-Sentiment-Scoring, ein Target-Backfill für Forward Returns und eine
monatliche Feature-Analyse mit Korrelationen und ML-Importance.

### Was daran bereits richtig gemacht ist

**Point-in-Time-Erfassung.** `analyst_ratings` läuft seit dem 17. März, `fundamentals_snapshot`
seit dem 16. April. Das sind fünf beziehungsweise vier Monate selbst aufgebauter Historie von
Daten, die kommerziell nur zu Enterprise-Konditionen rückwirkend zu bekommen sind. Genau das ist
der Ansatz, mit dem man sich als Einzelperson eine Datenbasis verschafft, die man nicht kaufen
kann — und du hast ihn ohne fremdes Zutun gefunden.

**Zielvariablen.** Der Target-Backfill für Forward Returns ist der Unterschied zwischen einem
Dashboard und einem Prognosesystem. Ohne Zielvariable kann man Features anschauen; mit ihr kann
man sie bewerten.

**Verzögerungserfassung.** Die `VERZÖG.`-Spalte bei den Politiker-Trades zeigt, dass das Problem
der Meldeverzögerung erkannt ist. Das übersehen die meisten.

**Feature-Store als eigene Ebene.** `feature_snapshots` getrennt von den Rohdaten zu halten, ist
die architektonisch saubere Lösung. Sie erlaubt es, Feature-Definitionen zu ändern, ohne die
Rohdaten anzufassen.

### Das Zielbild

Die App soll täglich fünf Kandidaten vorschlagen und je Kandidat eine Markdown-Datei erzeugen,
die alle Daten enthält, die *nicht* kostenlos im Web verfügbar sind. Der Skill `aktien-analyse`
liest diese Datei und ergänzt sie um qualitative Recherche zu Narrativ, Guidance, Regulierung und
Katalysatoren.

Diese Arbeitsteilung ist gut gewählt, weil sie beide Seiten das machen lässt, worin sie stark
sind. Kapitel 5 bis 7 beschreiben, wie sie konkret aussehen sollte.

---

## 2. Der kritische Befund: Survivorship Bias

Bevor irgendeine neue Datenquelle sinnvoll ist, muss eine Frage geklärt werden.

### Das Problem

`prices_daily` reicht bis November 2020 zurück. Der Job „Monthly Index Membership Sync
(S&P 500 + Nasdaq 100)" läuft seit 18 Tagen und hat 505 Einträge erzeugt. Das legt nahe, dass die
Index-Mitgliedschaft nur als aktueller Zustand vorliegt, nicht als Historie.

Wenn das Universum von 749 Titeln die *heutige* Indexzusammensetzung ist, dann enthält jedes
Training auf historischen Daten eine Information, die zum damaligen Zeitpunkt nicht verfügbar war:
das Wissen darüber, welche Unternehmen überlebt haben und stark genug waren, um im August 2026
noch im Index zu stehen.

Ein Modell, das auf dieser Basis 2021er-Renditen prognostiziert, hat einen unfairen Vorteil. Firmen,
die zwischen 2021 und 2026 pleitegingen, übernommen wurden oder aus dem Index flogen, kommen im
Trainingsdatensatz schlicht nicht vor. Die durchschnittliche Rendite im Datensatz ist dadurch nach
oben verzerrt, und Features, die eigentlich Insolvenzrisiko anzeigen, wirken harmlos — weil die
Insolvenzfälle fehlen.

### Die Größenordnung

Die Verzerrung ist in der Regel kein Randeffekt. Je nach Universum und Zeitraum bewegt sie sich
im Bereich mehrerer Prozentpunkte Jahresrendite an Scheinalpha. Bei einem Index wie dem S&P 500
mit typischerweise 20 bis 25 Änderungen pro Jahr summiert sich das über fünfeinhalb Jahre auf
über hundert Titel, die in deinem Datensatz fehlen oder fälschlich enthalten sind.

Praktische Konsequenz: Ein Backtest zeigt Alpha, das im Livebetrieb nicht auftritt, und man sucht
den Fehler dann im Modell statt in den Daten.

### Die Behebung

**Erstens, historische Index-Mitgliedschaft rekonstruieren.** Die Änderungshistorie des S&P 500
ist öffentlich dokumentiert und mit Datum versehen — sie lässt sich einmalig parsen und in eine
Tabelle mit `ticker`, `index`, `valid_from`, `valid_to` überführen. Für den Nasdaq 100 gilt
dasselbe.

**Zweitens, delistete Ticker nachladen.** Alpaca liefert über den Assets-Endpunkt auch inaktive
Symbole und bietet mit dem `asof`-Parameter ein historisches Symbol-Mapping, das Umbenennungen
korrekt auflöst. Der Bars-Endpunkt gibt für delistete Titel weiterhin Daten bis zum
Delisting-Zeitpunkt zurück.

**Drittens, die Feature-Berechnung auf das zum Stichtag gültige Universum umstellen.** Das ist
der Teil, der am meisten Code berührt, weil jede Cross-Sectional-Berechnung — Perzentile,
Rankings, relative Stärke — sich auf die damalige Grundgesamtheit beziehen muss.

Aufwand realistisch zwei bis vier Tage. Es ist die unattraktivste Aufgabe auf der gesamten Liste
und gleichzeitig die einzige, ohne die die anderen wenig wert sind.

### Der verwandte Befund: Lookahead

Dieselbe Logik gilt zeitlich innerhalb der Daten. Für jede Signal-Tabelle muss dokumentiert sein,
zu welchem Zeitpunkt die Information tatsächlich **verfügbar** war — nicht, auf welchen Zeitpunkt
sie sich bezieht.

| Tabelle | Bezugsdatum | Verfügbar ab |
|---|---|---|
| `insider_trades` | Transaktionsdatum | Filing-Datum (bis zu 2 Werktage später, oft mehr) |
| `politician_trades` | Trade-Datum | Disclosure-Datum (gesetzlich 45 Tage, faktisch bis zu 824) |
| `form13f_holdings` | Quartalsende | Quartalsende + ca. 45 Tage |
| `fundamentals_snapshot` | Abrufdatum | identisch — korrekt gelöst |
| `earnings_calendar` | Termin | Ankündigungsdatum, nicht der Termin selbst |
| `analyst_ratings` | Abrufdatum | identisch — korrekt gelöst |

Besonders `earnings_calendar` verdient Aufmerksamkeit: Die Tabelle reicht bis März 2027, enthält
also Zukunftsdaten. Beim Backtest darf ein Termin nur genutzt werden, wenn er zum Stichtag bereits
angekündigt war. Apple etwa kündigt seine Quartalstermine typischerweise etwa drei Wochen im
Voraus an — vorher existiert nur eine Schätzung.

Eine einheitliche Spalte `available_from` in allen Signal-Tabellen löst das strukturell und
verhindert, dass der Fehler bei jeder neuen Feature-Definition erneut auftritt.

---

## 3. Datenlücken nach Priorität

Die Auswahl folgt drei Kriterien: erwarteter Erklärungswert für Forward Returns, Kosten, und ob
Historie rückwirkend verfügbar ist oder erst aufgebaut werden muss. Der letzte Punkt entscheidet
über die Reihenfolge — was rückwirkend verfügbar ist, kann warten; was akkumuliert werden muss,
sollte sofort starten.

### 3.1 Estimate-Revisionen — die größte Einzellücke

**Was fehlt:** `analyst_ratings` erfasst nach allem, was von außen erkennbar ist, Ratings und
Kursziele. Der EPS- und Umsatz-Konsens sowie die Revisionszähler sind ein anderer Datensatz.

**Warum es wichtig ist:** Die Richtung, in die Analysten ihre Gewinnschätzungen bewegen, ist einer
der am besten dokumentierten kurzfristigen Prognosefaktoren. Der Mechanismus ist verhaltensbedingt
und deshalb erstaunlich stabil: Analysten korrigieren ihre Schätzungen nicht in einem Sprung,
sondern in Etappen, weil niemand als Einziger stark abweichen will. Eine begonnene Revisionsrichtung
setzt sich deshalb typischerweise über Wochen fort.

Zwei Aktien mit identischem heutigen Konsens können völlig verschiedene Dynamiken haben:

| | vor 90 T. | vor 60 T. | vor 30 T. | vor 7 T. | heute |
|---|---|---|---|---|---|
| Titel A | 7,90 | 8,20 | 8,45 | 8,70 | 8,80 |
| Titel B | 9,80 | 9,40 | 9,10 | 8,85 | 8,80 |

Ohne Revisions-Historie sind beide für dein Modell derselbe Datenpunkt.

**Quelle:** `yfinance` liefert über `eps_trend` und `eps_revisions` genau das — und zwar mit
**90 Tagen Rückwirkung**:

```python
import yfinance as yf
t = yf.Ticker("AAPL")

t.eps_trend        # current, 7daysAgo, 30daysAgo, 60daysAgo, 90daysAgo
t.eps_revisions    # upLast7days, upLast30days, downLast7days, downLast30days
t.earnings_estimate  # avg, low, high, numberOfAnalysts, yearAgoEps, growth
t.revenue_estimate
t.earnings_history   # epsEstimate, epsActual, epsDifference, surprisePercent
```

**Daraus abgeleitetes Feature:** Revisions-Momentum als
`(upLast30days − downLast30days) / (upLast30days + downLast30days)`, ein Wert zwischen −1 und +1.

**Warum das zeitkritisch ist:** Das 90-Tage-Fenster rollt. Was heute nicht abgeholt wird, ist in
drei Monaten unwiederbringlich verloren. Von allen Punkten in diesem Dokument ist das der einzige,
bei dem Verzögerung dauerhaften Schaden anrichtet.

**Vorbehalt, der dazugehört:** `yfinance` greift auf eine inoffizielle Schnittstelle zu, verstößt
gegen Yahoos Nutzungsbedingungen für kommerzielle Verwendung, hat keine Verfügbarkeitszusage und
kann jederzeit brechen. Für ein privates Analysewerkzeug vertretbar, für ein Produkt nicht. Die
lizenzierte Alternative mit praktisch identischem Datenmodell ist EODHD über den Endpunkt
`calendar/trends` für rund 50 USD monatlich im Jahresabo. Wer die Historie über 90 Tage hinaus
datumsgefiltert braucht, landet bei Massive in Verbindung mit Benzinga für 99 USD monatlich, dort
allerdings für Rating- und Kurszielkonsens, nicht für EPS.

### 3.2 Earnings Surprise und Post-Earnings-Announcement-Drift

`earnings_calendar` enthält Termine. Was fehlt, ist die Überraschungskomponente: der tatsächlich
berichtete Gewinn gegen die Erwartung.

Standardized Unexpected Earnings normiert diese Differenz auf die Streuung der Schätzungen:

```
SUE = (EPS_ist − EPS_konsens) / Standardabweichung der Einzelschätzungen
```

Der zugehörige Effekt — Kurse driften nach einer Überraschung noch wochenlang in Überraschungsrichtung
weiter — gehört zu den am längsten und breitesten dokumentierten Kapitalmarktanomalien. Er ist
seit den 1960er Jahren beschrieben, hat sich abgeschwächt, aber nicht aufgelöst.

Als Feature ergibt das eine Kombination aus SUE und der Anzahl Tage seit dem Bericht. `yfinance`
liefert über `earnings_history` mehrere Jahre rückwirkend, das ist also sofort verfügbar.

Ergänzend interessant und in denselben Daten enthalten: die **Beat-Historie** eines Unternehmens.
Manche Firmen übertreffen systematisch, weil sie konservativ führen — das ist eine Eigenschaft des
Managements, kein Zufall, und als Feature nutzbar.

### 3.3 Benchmarks und Sektorklassifikation

Diese beiden gehören zusammen, weil sie dasselbe ermöglichen: relative statt absoluter Betrachtung.

**Benchmarks.** Für relative Stärke brauchst du Vergleichsreihen in `prices_daily`: SPY, QQQ, IWM
sowie die elf GICS-Sektor-ETFs (XLK, XLF, XLV, XLE, XLY, XLP, XLI, XLB, XLU, XLRE, XLC). Alle über
Alpaca verfügbar, Historie ab 2016, kostenlos.

Warum das mehr wiegt als es klingt: Ein Titel, der acht Prozent verliert, während der Index vier
Prozent gewinnt, hat zwölf Prozentpunkte relativ eingebüßt. Das ist ein qualitativ anderes Signal
als ein Titel, der acht Prozent verliert, während der Index zehn Prozent verliert. Absolute
Kursveränderung vermischt Marktbewegung mit titelspezifischer Information; relative Stärke trennt
sie.

**Sektorklassifikation.** Ohne `sector` und `industry` in `universe` lernt das Modell Branchenrotation
und weist sie als Feature-Importance aus. Wenn im Trainingszeitraum Technologiewerte stark liefen,
werden alle Features, die mit Technologiezugehörigkeit korrelieren — hohe Bruttomarge, geringe
Verschuldung, hohes KGV — als prädiktiv erscheinen. Das ist kein Titel-Alpha, sondern ein
Sektor-Beta im Kostüm.

Die Behebung ist entweder eine Sektor-Neutralisierung beim Ranking (Perzentile innerhalb des
Sektors statt über das Gesamtuniversum) oder die Aufnahme des Sektors als kategoriales Feature,
damit das Modell den Effekt explizit modellieren kann statt ihn implizit zu absorbieren.

### 3.4 Short Interest

Zwei Wege, beide kostenlos:

**Massive** (der Anbieter, der bis vor kurzem Polygon.io hieß) bietet unter
`/stocks/fundamentals/short-interest` Ticker, Settlement Date, Short Interest, durchschnittliches
Tagesvolumen und Days to Cover. Im kostenlosen Tarif mit zwei Jahren Historie enthalten — also
sofort verfügbar, ohne Aufbauzeit.

**FINRA Developer Center** liefert die Rohdaten inklusive Reg-SHO-Tagesvolumen und ATS-Blocks.
Public Credentials kosten nichts, eine Registrierung ist nötig.

Als Feature zählt weniger das Niveau als die **Veränderung**. Ein Short Interest von einem Prozent
ist unauffällig; ein Anstieg von einem auf drei Prozent innerhalb eines Monats ist eine Aussage.
Umgekehrt ist fallendes Short Interest bei steigendem Kurs ein Eindeckungssignal.

### 3.5 Makro-Regime

Dein Modell arbeitet vermutlich rein querschnittlich: Es vergleicht Titel untereinander zu einem
Stichtag. Was dabei verloren geht, ist der Zustand des Gesamtmarktes.

Das ist relevant, weil Faktoren regimeabhängig wirken. Momentum funktioniert in trendenden Märkten
und bricht an Wendepunkten überdurchschnittlich stark zusammen. Qualität und niedrige Volatilität
wirken in Stressphasen, kosten aber in Erholungsrallyes Rendite. Ein Modell, das über verschiedene
Regime hinweg mittelt, lernt den Durchschnitt zweier gegensätzlicher Zusammenhänge — und der ist
oft nahe null.

Die FRED-API der Federal Reserve Bank of St. Louis ist kostenlos, gut dokumentiert und liefert
Historien über Jahrzehnte, also sofort rückwirkend für deinen kompletten Zeitraum:

| Serie | Inhalt | Aussage |
|---|---|---|
| `DGS2`, `DGS10` | Zinsen 2 und 10 Jahre | Zinsniveau; Differenz als Rezessionsindikator |
| `BAMLH0A0HYM2` | High-Yield-Spread | bester verfügbarer Risikoappetit-Indikator |
| `VIXCLS` | VIX | Volatilitätsregime |
| `DTWEXBGS` | Dollar-Index | Gegenwind für Exporteure, Rückenwind für Importeure |
| `T10YIE` | Inflationserwartung | Zinserwartung, Bewertungsdruck auf lange Cashflows |

Ein Collector, fünf bis sechs Serien, und du hast Regime-Features für den gesamten Zeitraum deiner
Preisdaten.

**Marktbreite** kommt kostenlos obendrauf, weil sie aus vorhandenen Daten berechenbar ist: Anteil
der Titel über der 50- und 200-Tage-Linie, Advance/Decline-Ratio, Anteil auf 52-Wochen-Hoch
beziehungsweise -Tief.

### 3.6 Optionsdaten und implizite Volatilität

Diese Daten liegen bereits in deinem bestehenden kostenlosen Alpaca-Zugang, werden aber
offensichtlich nicht genutzt. Der Endpunkt `/v1beta1/options/snapshots/{underlying}` liefert die
gesamte Optionskette mit Delta, Gamma, Theta, Vega, Rho und impliziter Volatilität. Historie ab
Februar 2024. Open Interest kommt separat aus dem Trading-Endpunkt `/v2/options/contracts`.

Daraus ableitbar:

- **ATM-IV** als Erwartungswert der Marktbewegung
- **IV-Rank und IV-Perzentil** — die Position der aktuellen IV in ihrer eigenen Jahresspanne. Das
  ist aussagekräftiger als das absolute IV-Niveau, weil es titelspezifisch normiert
- **Put/Call-Skew** — die IV-Differenz zwischen gleich weit aus dem Geld liegenden Puts und Calls.
  Ein direktes Maß für Absicherungsnachfrage
- **Terminstruktur** — kurze IV über langer IV signalisiert akuten Ereignisstress
- **IV/RV-Verhältnis** — preist der Optionsmarkt mehr oder weniger Bewegung ein, als tatsächlich
  stattfindet

IV-Rank braucht ein Jahr Vorlauf. Als Brücke lässt sich realisierte Volatilität sofort aus
`prices_daily` berechnen; das IV/RV-Verhältnis kommt dazu, sobald genug IV-Historie vorliegt.
Deshalb steht dieser Punkt in der Umsetzungsreihenfolge früher, als sein unmittelbarer Nutzen
nahelegt.

---

## 4. Aus vorhandenen Daten ableitbar

Das ist der günstigste Ertrag im ganzen Dokument, weil keine neue Quelle, kein neuer Collector und
keine neue Fehlerquelle entsteht.

**13F-Deltas.** Für ARK berechnest du bereits Veränderungen (`ark_deltas`, 10,4k Einträge). Für
`form13f_holdings` offenbar nicht. Dabei ist auch dort die Veränderung das Signal, nicht der
Bestand: Positionsveränderung je Fonds, Anzahl haltender Fonds und deren Veränderung,
Konzentration gemessen als Anteil der zehn größten Halter. Ein Titel, bei dem in einem Quartal
dreißig Fonds neu einsteigen, sagt etwas anderes als einer mit konstantem Bestand.

Zu beachten: 13F-Daten haben rund 45 Tage Verzögerung und decken nur Long-Positionen in
US-Aktien ab. Sie zeigen, was institutionelle Anleger vor sechs Wochen hielten — für einen
Swing-Horizont brauchbar, für kurzfristige Signale nicht.

**Insider-Ratio als kontinuierliches Feature.** `insider_clusters` existiert als Flag mit 401
Einträgen. Die zugrundeliegende Information lässt sich feiner nutzen: Kauf/Verkauf-Verhältnis je
Titel über 30, 90 und 180 Tage, nach Transaktionsvolumen gewichtet statt nach Anzahl. Wichtig ist
dabei die Trennung planmäßiger 10b5-1-Verkäufe von diskretionären Transaktionen — erstere haben
kaum Informationsgehalt, weil sie Monate vorher festgelegt wurden.

Generell gilt: Insiderkäufe sind deutlich aussagekräftiger als Insiderverkäufe. Für einen Verkauf
gibt es viele Gründe (Steuern, Diversifikation, Hauskauf); für einen Kauf im Wesentlichen einen.

**Sentiment-Momentum.** Der FinBERT-Score liegt vor. Was fehlt, ist seine Veränderungsrate über
7 und 30 Tage sowie das Nachrichtenvolumen relativ zum titelüblichen Durchschnitt. Bei Sentiment
ist die Änderung fast immer trennschärfer als das Niveau — ein Titel mit dauerhaft leicht negativer
Berichterstattung ist normal, ein Titel, dessen Sentiment innerhalb von zwei Wochen kippt, ist
ein Ereignis.

**Liquiditätsmaße.** Dollar-Volumen im 20-Tage-Mittel und Amihud-Illiquidität
(durchschnittliche absolute Rendite pro Dollar Handelsvolumen). Beides dient doppelt: als Feature,
weil illiquide Titel eine Risikoprämie tragen, und als Filter in der Kandidatenauswahl, damit keine
Titel vorgeschlagen werden, die praktisch nicht handelbar sind.

---

## 5. Architektur der Kandidaten-Pipeline

```
  feature_snapshots (täglich, je Ticker)
            │
            ▼
  ┌───────────────────────┐
  │  Cross-sectionales    │  Ranking über das zum Stichtag
  │  Scoring / ML-Modell  │  gültige Universum
  └───────────┬───────────┘
              ▼
  ┌───────────────────────┐
  │  Guardrails           │  Liquidität · Sektorlimit · Korrelation
  │                       │  Churn-Sperre · Mindest-Score
  └───────────┬───────────┘
              ▼
  ┌───────────────────────┐
  │  candidate_selections │  append-only, mit Feature-Attribution
  └───────────┬───────────┘
              ▼
  ┌───────────────────────┐
  │  Context-Pack-        │  1 MD je Kandidat + Tagesübersicht
  │  Generator            │
  └───────────┬───────────┘
              ▼
  ┌───────────────────────┐
  │  Skill                │  liest Pack → ergänzt Web-Recherche
  │  aktien-analyse       │  → vollständiger Analystenbericht
  └───────────────────────┘
```

### 5.1 Guardrails — ohne die ist die Auswahl unbrauchbar

Ein reines Top-5-nach-Score liefert in der Praxis regelmäßig unbrauchbare Ergebnisse. Fünf
notwendige Einschränkungen:

**Liquiditätsuntergrenze.** Ein Mindest-Dollar-Volumen im 20-Tage-Mittel, etwa 20 Millionen USD.
Ohne diesen Filter schlägt das Modell Titel vor, bei denen die eigene Order den Kurs bewegt.

**Sektorlimit.** Maximal zwei Kandidaten je Sektor. Andernfalls liefert das System an Tagen mit
starker Sektorrotation fünf Varianten desselben Trades.

**Korrelationsschwelle.** Paarweise Korrelation der Kandidaten über die letzten 60 Handelstage
begrenzen. Fünf Titel mit 0,9er Korrelation sind eine Position, keine fünf.

**Churn-Sperre.** Kein Titel, der in den letzten N Tagen bereits vorgeschlagen wurde. Sonst
erscheint derselbe Kandidat wochenlang und erzeugt den Eindruck von Bestätigung, wo nur Trägheit
im Feature-Set ist.

**Mindest-Score.** Der wichtigste Punkt: Wenn weniger als fünf Titel eine definierte Schwelle
reißen, sollen weniger vorgeschlagen werden. **„Heute keine Kandidaten" ist ein gültiges Ergebnis.**

Das entspricht genau der Regel, die auch der Analyse-Skill anwendet: Wenn das
Chance-Risiko-Verhältnis unter der Schwelle liegt, lautet die Antwort „kein Einstieg", nicht „der
beste von fünf schlechten". Ein System, das jeden Tag zwingend fünf Namen ausspuckt, erzeugt an
schlechten Tagen Handel um des Handels willen — und das ist die teuerste Eigenschaft, die ein
Auswahlsystem haben kann.

Ergänzend: Titel mit Quartalszahlen innerhalb der nächsten fünf Handelstage sollten **markiert,
aber nicht ausgeschlossen** werden. Ein bevorstehender Termin ist ein Risiko, aber auch ein
Katalysator — die Entscheidung darüber gehört in die Analyse, nicht in den Filter.

### 5.2 Feature-Attribution

Jede Auswahl muss speichern, **warum** ein Titel ausgewählt wurde — welche Features mit welchem
Beitrag zum Score geführt haben. Bei Baum-Verfahren liefern SHAP-Werte das direkt, bei linearen
Modellen die gewichteten Beiträge.

Zwei Gründe: Erstens ist ein Vorschlag ohne Begründung nicht überprüfbar, und ein System, das man
nicht überprüfen kann, wird man entweder blind befolgen oder ignorieren — beides schlecht.
Zweitens ist die Attribution ein zentraler Bestandteil des Context Packs und damit die Grundlage
dafür, dass der Skill eine These formulieren kann statt nur Daten zu referieren.

---

## 6. Spezifikation des Context Packs

### 6.1 Das Grundprinzip

Das Context Pack soll genau das enthalten, was **die Web-Recherche nicht liefern kann**. Alles
andere gehört nicht hinein, weil es das Dokument aufbläht und der Skill es ohnehin selbst holt.

| Die App liefert | Das Web liefert |
|---|---|
| Cross-sectionale Perzentile über das eigene Universum | Aktuelles Narrativ und Nachrichtenlage |
| Eigene Point-in-Time-Historie seit März/April 2026 | Guidance im Wortlaut aus dem Earnings Call |
| Signal-Stack mit korrekten Verfügbarkeitsdaten | Regulatorische und rechtliche Situation |
| Modell-Score und Feature-Attribution | Termine und Katalysatoren |
| Historische Analogie aus Forward Returns | Begründungen einzelner Analystenhäuser |
| Peer-Set, aus dem echten Universum berechnet | Qualitative Wettbewerbsposition |

### 6.2 Der eigentliche Mehrwert: die historische Analogie

Von allen sechs Blöcken ist einer qualitativ anders als der Rest. Du hast Forward-Return-Targets
über den gesamten Zeitraum. Damit kannst du eine Frage beantworten, die keine noch so gute
Web-Recherche beantworten kann:

> *Wann immer historisch eine ähnliche Feature-Konstellation auftrat — was ist in den folgenden
> 20 und 60 Handelstagen passiert, mit welcher Trefferquote, bei welcher Stichprobengröße?*

Das ist die belastbare Form dessen, was in der Analystenpraxis „variant perception" heißt: eine
begründete Abweichung von der Konsenserwartung. Ohne sie ist jede Trade-These eine Meinung; mit ihr
wird sie überprüfbar.

Wichtig ist die ehrliche Angabe der Stichprobengröße. Eine Trefferquote von 70 Prozent bei acht
historischen Fällen ist bedeutungslos. Bei zweihundert Fällen ist sie eine Aussage. Das Context
Pack sollte beides nennen und bei zu kleiner Stichprobe explizit darauf hinweisen, statt eine
Prozentzahl ohne Kontext zu liefern.

### 6.3 Struktur der Datei

Vorschlag: ein Verzeichnis je Handelstag mit fünf Kandidatendateien plus einer Übersicht.

```
context_packs/
└── 2026-08-19/
    ├── 00_uebersicht.md          Rangliste, Marktregime, Guardrail-Protokoll
    ├── 01_NVDA.md
    ├── 02_MSFT.md
    ├── 03_UNH.md
    ├── 04_CAT.md
    └── 05_JPM.md
```

Die Übersicht ist wichtig, weil sie den Kontext trägt, der für alle fünf gilt: Marktregime,
Breite, welche Titel knapp an den Guardrails gescheitert sind, und ob überhaupt fünf die
Mindestschwelle gerissen haben.

Jede Kandidatendatei beginnt mit YAML-Frontmatter für die maschinenlesbare Auswertung und enthält
danach die menschenlesbare Aufbereitung.

### 6.4 Vorlage einer Kandidatendatei

````markdown
---
ticker: NVDA
company: NVIDIA Corporation
as_of: 2026-08-19
rank: 1
model_score: 0.847
score_percentile: 99.6
universe_size: 503
sector: Information Technology
industry: Semiconductors
close: 225.01
market_cap_bn: 5450
adv_20d_musd: 8420
earnings_in_days: 34
earnings_confirmed: false
regime: risk_on
data_completeness: 0.94
---

# NVDA — Context Pack, 19.08.2026

Rang 1 von 5 · Score 0,847 (99,6. Perzentil von 503 Titeln)

## 1. Warum das Modell diesen Titel gewählt hat

| Feature | Wert | Perzentil | Beitrag zum Score |
|---|---|---|---|
| revisions_momentum_30d | +0,71 | 97 | +0,182 |
| rel_strength_3m_vs_sector | +8,4 Pp | 91 | +0,143 |
| sue_last | +2,3 | 94 | +0,097 |
| insider_buy_ratio_90d | 0,62 | 88 | +0,061 |
| iv_rank | 22 | 14 | +0,044 |
| short_interest_change_1m | −0,4 Pp | 71 | +0,022 |
| peg_ratio | 0,51 | 8 | −0,031 |

Gegenläufig: Bewertung im obersten Dezil, Sektorkonzentration erhöht.

## 2. Cross-sectionale Einordnung

Perzentile über das zum Stichtag gültige Universum (503 Titel), nicht über
den heutigen Indexstand.

| Kennzahl | Wert | Perzentil Universum | Perzentil Sektor |
|---|---|---|---|
| KGV forward | 22,54 | 61 | 34 |
| PEG | 0,51 | 8 | 6 |
| EV/EBITDA | 32,68 | 88 | 71 |
| Umsatzwachstum TTM | +70,7 % | 99 | 98 |
| FCF-Marge | 46,97 % | 97 | 94 |
| ROIC | — | — | — |

## 3. Eigene Point-in-Time-Historie

Was die App seit Beginn der Erfassung beobachtet hat. Diese Reihen sind
online nicht rekonstruierbar.

### EPS-Konsens (Erfassung seit 17.03.2026)

| Stichtag | FY27E EPS | Analysten | Δ zum Vormonat |
|---|---|---|---|
| 17.03.2026 | 4,12 | 58 | — |
| 17.05.2026 | 4,34 | 60 | +5,3 % |
| 17.07.2026 | 4,71 | 61 | +8,5 % |
| 18.08.2026 | 4,89 | 62 | +3,8 % |

Fünf Monate ununterbrochene Aufwärtsrevision.

### Rating-Verteilung (Erfassung seit 17.03.2026)

| Stichtag | Strong Buy | Buy | Hold | Sell | Ø Kursziel |
|---|---|---|---|---|---|
| 17.03.2026 | 24 | 21 | 9 | 2 | 198 |
| 18.08.2026 | 31 | 19 | 6 | 1 | 264 |

### Fundamentaldaten-Drift (Erfassung seit 16.04.2026)

Veränderung der Kernkennzahlen seit Erfassungsbeginn, damit erkennbar wird,
ob sich die Substanz oder nur der Kurs bewegt hat.

## 4. Signal-Stack

Alle Einträge mit Verfügbarkeitsdatum, nicht mit Bezugsdatum.

| Signal | Wert | Verfügbar seit | Lag |
|---|---|---|---|
| ARK-Delta 30 T. | +142k Stück | 18.08.2026 | 1 T. |
| Insider Kauf/Verkauf 90 T. | 0,62 (volumengewichtet) | 15.08.2026 | 2 T. |
| davon diskretionär | 0,71 | | |
| 13F: Anzahl Halter Q2 | 1.842 (+87) | 14.08.2026 | 45 T. |
| Politiker-Trades | keine mit Lag < 45 T. | — | — |
| Short Interest | 0,9 % Float (−0,4 Pp) | 15.08.2026 | 3 T. |
| Sentiment 30 T. | +0,34 (Δ +0,11) | 18.08.2026 | 0 T. |

## 5. Technik und Volatilität

Bereits berechnete Indikatoren aus `technical_indicators`, ergänzt um
relative Stärke gegen Index und Sektor.

| | Wert | vs. S&P 500 | vs. Sektor |
|---|---|---|---|
| Rendite 1 M. | +6,2 % | +1,8 Pp | −0,4 Pp |
| Rendite 3 M. | +18,9 % | +14,3 Pp | +8,4 Pp |
| Rendite 12 M. | +41,2 % | +19,2 Pp | +11,7 Pp |

Gleitende Durchschnitte, RSI, ATR, IV-Rank, Skew analog.

## 6. Historisches Analogon

Als diese Feature-Konstellation im Trainingszeitraum auftrat — definiert als
Revisions-Momentum über 0,5, relative Stärke 3 M. über dem 85. Perzentil und
IV-Rank unter 30:

| Horizont | Ø Rendite | Median | Trefferquote | Stichprobe |
|---|---|---|---|---|
| 20 Handelstage | +2,8 % | +2,1 % | 61 % | 847 Fälle |
| 60 Handelstage | +6,4 % | +4,9 % | 64 % | 812 Fälle |

Benchmark im selben Zeitraum: +1,1 % / +3,4 %.

Einschränkung: Der Trainingszeitraum umfasst 11/2020 bis 08/2026 und enthält
überwiegend steigende Märkte. Die Trefferquote ist regimeabhängig.

## 7. Peer-Set

Aus dem Universum berechnet, nicht handverlesen — die fünf nach Sektor,
Marktkapitalisierung und Umsatzwachstum ähnlichsten Titel, mit denselben
Kennzahlen zum Vergleich.

## 8. Marktkontext

| | Wert | Perzentil 1 J. |
|---|---|---|
| VIX | 16,2 | 34 |
| High-Yield-Spread | 289 bp | 21 |
| Zinsstruktur 10y−2y | +0,74 | 78 |
| Titel über 200-Tage-Linie | 68 % | 71 |
| Regime-Klassifikation | risk_on | |

## 9. Datenqualität

| Block | Vollständigkeit | Anmerkung |
|---|---|---|
| Preise / Technik | 100 % | |
| Estimates | 100 % | Historie erst ab 17.03.2026 |
| Fundamentals | 100 % | Historie erst ab 16.04.2026 |
| 13F | 100 % | Stand 30.06.2026, 45 T. Lag |
| Optionen / IV | 62 % | IV-Rank noch nicht belastbar, < 1 J. Historie |
| Politiker | 0 % | keine Einträge mit verwertbarem Lag |

**Was dieses Pack NICHT enthält und der Skill recherchieren muss:**
aktuelles Narrativ, Guidance im Wortlaut, Regulierung und Rechtsstreitigkeiten,
bestätigte Termine, Begründungen einzelner Analystenhäuser, Wettbewerbsposition,
Management-Veränderungen.
````

### 6.5 Warum YAML-Frontmatter

Der Skill soll die Kennzahlen zuverlässig parsen können, ohne Tabellen im Fließtext interpretieren
zu müssen. Frontmatter macht die wichtigsten Werte maschinenlesbar, während der Rest des Dokuments
für dich lesbar bleibt. Das Feld `data_completeness` ist dabei mehr als Kosmetik: Der Skill soll
wissen, wie belastbar die Grundlage ist, und das im Bericht ausweisen können.

---

## 7. Anpassung des Analyse-Skills

Der bestehende Skill `aktien-analyse` bekommt einen zweiten Betriebsmodus.

**Modus A — heute:** Ticker rein, vollständige Web-Recherche, Bericht raus.

**Modus B — neu:** Context Pack rein, nur noch die qualitativen Lücken recherchieren, zusammenführen.

Der Ablauf in Modus B:

1. Context Pack lesen, Frontmatter parsen, `data_completeness` prüfen
2. Aus dem Pack ableiten, welche Blöcke fehlen oder unvollständig sind
3. Gezielt nur diese Lücken per Web recherchieren — Narrativ, Guidance-Wortlaut, Regulierung,
   Termine, Analystenbegründungen
4. Die Modell-Attribution als Ausgangsthese behandeln, nicht als Ergebnis. Der Skill muss prüfen,
   ob die qualitative Lage die quantitative Sicht stützt oder ihr widerspricht
5. Widersprüche explizit benennen. Wenn das Modell wegen Aufwärtsrevisionen kauft, die Web-Recherche
   aber eine Guidance-Senkung findet, die noch nicht in den Schätzungen angekommen ist, ist genau
   das der wertvollste Befund des ganzen Berichts
6. Bericht nach der bestehenden Struktur bauen — mit einem zusätzlichen Kapitel „Modell-Sicht
   gegen qualitative Lage"

**Was unverändert bleibt:** die Entscheidungslogik. CRV-Schwelle, Invalidierungspunkt,
Positionsgröße nach ATR, und die Regel, dass „kein Einstieg" ein gültiges Ergebnis ist. Ein Modell,
das einen Titel auf Rang 1 setzt, ist ein Argument — kein Freibrief, die Risikoprüfung zu
überspringen. Wenn das Chance-Risiko-Verhältnis am aktuellen Kurs schlecht ist, bleibt die Antwort
„warten", auch bei Score 0,847.

Dieser Punkt ist wichtiger, als er klingt. Ein Auswahlsystem erzeugt psychologischen Druck, den
Vorschlag auch umzusetzen. Die Trennung zwischen „das Modell hält diesen Titel für interessant"
und „zu diesem Kurs ist das ein guter Trade" muss architektonisch erzwungen werden, nicht
disziplinarisch.

---

## 8. Schema-Vorschläge

Im Stil der bestehenden Tabellen, PostgreSQL angenommen. Durchgängig append-only mit `as_of`.

```sql
-- Historische Index-Mitgliedschaft (behebt Survivorship Bias)
CREATE TABLE index_membership (
    id          BIGSERIAL PRIMARY KEY,
    ticker      TEXT        NOT NULL,
    index_name  TEXT        NOT NULL,          -- 'SP500' | 'NDX'
    valid_from  DATE        NOT NULL,
    valid_to    DATE,                          -- NULL = aktuell Mitglied
    source      TEXT        NOT NULL,
    UNIQUE (ticker, index_name, valid_from)
);
CREATE INDEX ix_membership_lookup ON index_membership (index_name, valid_from, valid_to);

-- Analystenschätzungen, Point-in-Time
CREATE TABLE estimates_snapshot (
    id             BIGSERIAL PRIMARY KEY,
    ticker         TEXT        NOT NULL,
    as_of          TIMESTAMPTZ NOT NULL,       -- Abrufzeitpunkt
    period         TEXT        NOT NULL,       -- '0q','+1q','0y','+1y'
    eps_avg        NUMERIC,
    eps_low        NUMERIC,
    eps_high       NUMERIC,
    eps_n_analysts INTEGER,
    eps_7d_ago     NUMERIC,
    eps_30d_ago    NUMERIC,
    eps_60d_ago    NUMERIC,
    eps_90d_ago    NUMERIC,
    rev_up_7d      INTEGER,
    rev_up_30d     INTEGER,
    rev_down_7d    INTEGER,
    rev_down_30d   INTEGER,
    revenue_avg    NUMERIC,
    source         TEXT        NOT NULL,
    raw            JSONB,                      -- vollständige Rohantwort
    UNIQUE (ticker, as_of, period, source)
);

-- Short Interest
CREATE TABLE short_interest (
    id              BIGSERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    settlement_date DATE NOT NULL,
    available_from  DATE NOT NULL,             -- Veröffentlichungsdatum
    short_interest  BIGINT,
    avg_daily_volume BIGINT,
    days_to_cover   NUMERIC,
    pct_of_float    NUMERIC,
    source          TEXT NOT NULL,
    UNIQUE (ticker, settlement_date, source)
);

-- Makro-Regime
CREATE TABLE macro_series (
    id         BIGSERIAL PRIMARY KEY,
    series_id  TEXT NOT NULL,                  -- 'DGS10','VIXCLS','BAMLH0A0HYM2',...
    obs_date   DATE NOT NULL,
    value      NUMERIC,
    as_of      TIMESTAMPTZ NOT NULL,           -- FRED revidiert Werte nachträglich
    UNIQUE (series_id, obs_date, as_of)
);

-- Optionen / implizite Volatilität
CREATE TABLE options_iv_snapshot (
    id            BIGSERIAL PRIMARY KEY,
    ticker        TEXT        NOT NULL,
    as_of         TIMESTAMPTZ NOT NULL,
    atm_iv_30d    NUMERIC,
    atm_iv_60d    NUMERIC,
    iv_rank       NUMERIC,
    iv_percentile NUMERIC,
    skew_25d      NUMERIC,                     -- IV Put 25Δ minus IV Call 25Δ
    term_slope    NUMERIC,                     -- IV 60d minus IV 30d
    total_oi_call BIGINT,
    total_oi_put  BIGINT,
    put_call_oi   NUMERIC,
    UNIQUE (ticker, as_of)
);

-- Kandidatenauswahl
CREATE TABLE candidate_selections (
    id            BIGSERIAL PRIMARY KEY,
    selection_date DATE       NOT NULL,
    ticker        TEXT        NOT NULL,
    rank          INTEGER     NOT NULL,
    model_score   NUMERIC     NOT NULL,
    score_pctile  NUMERIC,
    model_version TEXT        NOT NULL,
    attribution   JSONB       NOT NULL,        -- Feature -> Beitrag
    guardrails    JSONB,                       -- welche Filter griffen
    context_pack_path TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (selection_date, ticker)
);

-- Protokoll der nicht ausgewählten Titel, für spätere Auswertung
CREATE TABLE candidate_rejections (
    id             BIGSERIAL PRIMARY KEY,
    selection_date DATE NOT NULL,
    ticker         TEXT NOT NULL,
    model_score    NUMERIC,
    rejected_by    TEXT NOT NULL,              -- 'liquidity','sector_cap','churn',...
    UNIQUE (selection_date, ticker)
);
```

Zwei Entwurfsentscheidungen, die sich später auszahlen:

**`raw JSONB` in jeder Snapshot-Tabelle.** Wenn du in einem Jahr merkst, dass du ein Feld brauchst,
das du damals nicht geparst hast, ist es ohne die Rohantwort unwiederbringlich weg. Der
Speicherplatz ist gemessen am Nutzen vernachlässigbar.

**`candidate_rejections`.** Zu protokollieren, welche Titel an welchem Guardrail gescheitert sind,
erlaubt später die Frage: Hätten die abgelehnten Kandidaten besser abgeschnitten? Ohne dieses Log
lässt sich nie prüfen, ob die Guardrails helfen oder schaden.

---

## 9. Methodische Fallstricke

Diese Punkte betreffen nicht die Datenbeschaffung, sondern das Modellieren. Sie sind der häufigste
Grund, warum gut gebaute Pipelines am Ende keine verwertbaren Signale liefern.

**Multiple Testing.** Bei fünfzig oder mehr Features findet man immer etwas, das im Backtest
funktioniert. Der Schutz ist ein strikt getrennter Out-of-Sample-Zeitraum, der nie zur
Modellauswahl herangezogen wird, und Walk-Forward-Validierung statt eines einfachen
Train/Test-Splits. Wer denselben Testzeitraum zwanzigmal ansieht, hat ihn zum Trainingszeitraum
gemacht.

**Transaktionskosten und Slippage.** Ein Backtest ohne Kostenmodell überschätzt die Rendite
systematisch, und zwar umso stärker, je kürzer der Horizont ist. Bei Forward Returns über zwanzig
Handelstage und täglicher Neuauswahl summiert sich das schnell auf mehrere Prozentpunkte pro Jahr.

**Regime-Overfitting.** Der Zeitraum 11/2020 bis 08/2026 enthält eine sehr spezifische Marktphase.
Ein Modell, das nur diese kennt, hat gelernt, was in dieser Phase funktionierte. Die
Makro-Regime-Features aus Abschnitt 3.5 helfen, den Effekt sichtbar zu machen — beseitigen ihn
aber nicht.

**Multikollinearität.** `technical_indicators` und `prices_daily` tragen dieselbe Information
mehrfach. Gleitende Durchschnitte verschiedener Längen sind hochkorreliert. Das verzerrt
Importance-Maße erheblich: Ein wichtiges Feature kann niedrig ranken, weil sein Beitrag auf fünf
korrelierte Varianten aufgeteilt wird.

**Feature-Stabilität.** Deine monatliche Importance-Analyse läuft bereits. Sinnvoll wäre die
Zusatzfrage, ob die Rangfolge über die Zeit stabil bleibt. Ein Feature, das jeden Monat auf einem
anderen Platz landet, ist wahrscheinlich Rauschen — unabhängig davon, wie gut es im
Gesamtzeitraum aussieht.

---

## 10. Umsetzungsreihenfolge

| # | Was | Warum an dieser Stelle | Aufwand |
|---|---|---|---|
| 1 | Estimates-Collector | Zeitkritisch, rollierendes 90-Tage-Fenster | 0,5 T. |
| 2 | Survivorship Bias beheben | Blocker für jede Modellvalidierung | 2–4 T. |
| 3 | Lookahead-Prüfung, `available_from` | Gleicher Grund, geringerer Aufwand | 1–2 T. |
| 4 | FRED-Regime-Serien | Einmal gebaut, sofort volle Historie | 0,5 T. |
| 5 | Benchmarks und Sektorklassifikation | Voraussetzung für relative Stärke | 1 T. |
| 6 | SUE und Short Interest | Beide rückwirkend verfügbar | 1 T. |
| 7 | Abgeleitete Features (13F-Deltas, Ratios) | Kein neuer Collector nötig | 1–1,5 T. |
| 8 | Datenqualität (Lag-Gewichtung, Parser) | Parallel möglich, kleine Brocken | 1,5 T. |
| 9 | Optionen und IV | IV-Rank braucht Vorlauf, daher früh starten | 1–2 T. |
| 10 | Kandidaten-Pipeline und Context Pack | Erst wenn die Datenbasis steht | 4–6 T. |
| 11 | Skill-Anpassung Modus B | Nach dem Context-Pack-Format | 1 T. |

Die Reihenfolge folgt zwei Regeln: Was verloren geht, wenn man wartet, kommt zuerst. Was die
Gültigkeit von allem anderen bestimmt, kommt als zweites.

---

## 11. Was ich bewusst nicht empfehle

| Was | Kosten | Warum nicht |
|---|---|---|
| Dark-Pool-Daten | ab ca. 150 USD/Mon. | Vorhersagehorizont Stunden bis Tage, passt nicht zu einem Tagesmodell mit 20-Tage-Targets |
| Fertiger Options-Flow | ab ca. 150 USD/Mon. | Gleiches Argument; die Rohdaten liegen bei Alpaca im kostenlosen Zugang |
| Intraday-Tickdaten | Speicher | Enormer Speicherbedarf ohne Nutzen für die aktuellen Zielvariablen |
| Zacks / Refinitiv-Konsens | vierstellig monatlich | Nur Enterprise-Lizenzierung, Preise nicht öffentlich |
| Alpaca Algo Trader Plus | 99 USD/Mon. | Der kostenlose Tarif liefert historische SIP-Daten, solange `end` mindestens 15 Minuten zurückliegt. Für ein Tagesmodell reicht das |

Order Flow bleibt eine Option für später: Aus Alpaca-Trades mit Condition Codes und den
zugehörigen NBBO-Quotes lässt sich die Lee-Ready-Klassifikation selbst rechnen und daraus ein
Cumulative Volume Delta ableiten. Das ist Rechen- und Speicheraufwand, kein Datenzugangsproblem —
sinnvoll erst, wenn kürzere Horizonte dazukommen.

---

## 12. Zusammenfassung

Die App ist weiter, als der Begriff „Tracker" nahelegt: eine Feature-Pipeline mit Zielvariablen,
Point-in-Time-Erfassung und Importance-Analyse. Der Aufbau eigener Snapshot-Historie seit März
und April ist genau der Ansatz, mit dem man sich Daten verschafft, die kommerziell nur zu
Enterprise-Konditionen zu bekommen sind.

Drei Dinge stehen zwischen dem heutigen Stand und dem Zielbild:

**Erstens** die Frage der Survivorship-Freiheit des Universums. Sie entscheidet darüber, ob die
Modellvalidierung überhaupt aussagekräftig ist, und sollte vor jeder weiteren Datenquelle geklärt
werden.

**Zweitens** die Estimate-Revisionen. Sie sind die größte inhaltliche Lücke, kostenlos verfügbar,
bringen 90 Tage Historie geschenkt mit — und jeder Tag Verzögerung kostet unwiederbringlich einen
Tag Reihe.

**Drittens** die Guardrails der Kandidatenauswahl, insbesondere die Regel, dass „heute keine
Kandidaten" ein gültiges Ergebnis ist. Ein System, das täglich fünf Namen liefern *muss*, erzeugt
an schlechten Tagen Handel ohne Grundlage.

Der eigentliche Hebel für die Berichtsqualität liegt aber woanders: in der historischen Analogie.
Die Kombination aus gespeicherten Feature-Konstellationen und Forward-Return-Targets erlaubt eine
Aussage, die keine Web-Recherche liefern kann — was historisch geschah, wenn es so aussah wie
heute. Genau das ist die Grundlage, auf der eine überprüfbare These steht statt einer Meinung.

---

*Erstellt am 18.08.2026 · Keine Anlageberatung · Aufwandsschätzungen sind grob und setzen
Vertrautheit mit der eigenen Codebasis voraus · Annahmen zu Technologie-Stack und Datenmodell
sind in Kapitel 8 und in der begleitenden Prüfliste gekennzeichnet*
