# DATA_SOURCES.md – Katalog der Datenquellen

> Detaillierte Dokumentation jeder Datenquelle: wie man sie abruft, was sie liefert, welche Einschränkungen sie hat.
> Wird erweitert, sobald neue Quellen hinzukommen.

---

## Übersicht

| Quelle | Kategorie | Kosten | Frequenz | Verzögerung | Sprint | Status |
|---|---|---|---|---|---|---|
| **Alpaca Market Data** | Preise (OHLCV) | Kostenlos (IEX) | Täglich | ~Minuten nach Close | 1b | ✅ Primär |
| **yfinance** | Preise (Fallback), Fundamentals | Kostenlos | Täglich | ~20 Min. EOD | 1 | ⏸ Fallback |
| **arkfunds.io** | Smart Money (ARK ETFs) | Kostenlos | Täglich EOD | ~1 Stunde | 2 | ✅ Aktiv |
| **SEC EDGAR – Form 4** | Insider-Trades | Kostenlos | Rolling | 2 Werktage (gesetzlich) | 3 | ✅ Aktiv |
| **SEC EDGAR – Form 13F** | Institutionelle Holdings | Kostenlos | Quartalsweise | Bis 45 Tage | 3 | ✅ Aktiv |
| **Senate eFD** | Politiker-Trades | Kostenlos | Wöchentlich | 30–45 Tage | 4 | ✅ Aktiv |
| **yfinance – Fundamentals** | P/E, Revenue, EPS | Kostenlos | Wöchentlich | ~ | 5 | ✅ Aktiv |
| **yfinance – Ratings** | Analyst-Upgrades/Downgrades | Kostenlos | Täglich | ~ | 5 | ✅ Aktiv |
| **yfinance – Earnings** | Earnings-Termine + Surprises | Kostenlos | Wöchentlich | ~ | 5 | ✅ Aktiv |

---

## 1. Alpaca Market Data API (Primäre Preisquelle)

**Kategorie:** Marktdaten (OHLCV)
**API-Endpoint:** `https://data.alpaca.markets/v2/stocks/bars`
**Doku:** https://docs.alpaca.markets/reference/stockbars
**Status:** ✅ Implementiert (Sprint 1b)

### Was es liefert

Multi-Symbol-Batch-Endpoint mit täglichen OHLCV-Daten:

- `o` = Open, `h` = High, `l` = Low, `c` = Close (adjustiert mit `adjustment=all`)
- `v` = Volume, `vw` = VWAP, `n` = Trade Count
- **adj_close = close** (Alpaca liefert mit `adjustment=all` bereits split- und dividenden-adjustierte Werte)

### Konfiguration

- **Feed:** `iex` (kostenlos im Free Tier)
- **Batch-Size:** 100 Ticker pro Request
- **644 Ticker in 7 Requests (<10s Gesamtlaufzeit)**
- **Rate Limit:** 200 req/min (Free Tier) → kein Problem

### Best Practices

- Multi-Symbol-Endpoint bevorzugen (vs. Einzelabfragen)
- `adjustment=all` für split+dividend-adjustierte Werte
- Pagination via `next_page_token` bei großen Zeiträumen
- Retry mit exponentiellem Backoff bei 429/5xx

---

## 2. yfinance (Fallback für Preise + Fundamentals)

**Kategorie:** Marktdaten (Fallback), Fundamentals, Ratings, Earnings
**Python-Paket:** `yfinance`
**Status:** ✅ Aktiv für Fundamentals/Ratings/Earnings (Sprint 5), ⏸ Fallback für Preise

> **Seit Sprint 1b:** yfinance wurde als primäre Preisquelle durch Alpaca ersetzt.
> Grund: Alpaca ist die offizielle Trading-Plattform, liefert konsistente Kurse.
> **Sprint 5:** yfinance wird aktiv für Fundamentals, Analyst-Ratings und Earnings-Kalender genutzt.
> **Post-Sprint 7:** yfinance wird zusätzlich für Sektor/Branche-Enrichment genutzt (Universe-Anreicherung).

### Was es liefert

**Fundamentals (via `ticker.info`):**
- `marketCap`, `trailingPE`, `forwardPE`, `priceToSalesTrailing12Months`, `priceToBook`
- `enterpriseToEbitda`, `profitMargins`, `operatingMargins`, `returnOnEquity`
- `totalRevenue`, `revenueGrowth`, `trailingEps`, `debtToEquity`, `currentRatio`
- `dividendYield`, `beta`

**Analyst-Ratings (via `ticker.upgrades_downgrades`):**
- `Firm` (z.B. Goldman Sachs), `ToGrade`, `FromGrade`, `Action` (up/down/main/init)
- Index = Datum der Rating-Änderung
- Lookback: 30 Tage

**Earnings-Kalender (via `ticker.get_earnings_dates()`):**
- `EPS Estimate`, `Reported EPS`, `Surprise(%)`
- Index = Earnings-Datum
- Limit: letzte 4 Earnings pro Ticker

**EPS Growth (via `ticker.get_earnings_estimate()`):**
- `growth`-Feld für aktuelles Quartal (0q)

**Sektor/Branche (via `ticker.info`):**
- `sector` (z.B. "Technology", "Healthcare", "Financials")
- `industry` (z.B. "Semiconductors", "Drug Manufacturers—General")
- Genutzt für Universe-Enrichment: Füllt fehlende Sektor-/Branchendaten auf
- Auslösung: Manuell via Settings-UI oder CLI-Script

### Konfiguration (Sprint 5)

- **Rate-Limiting:** 0.5s zwischen Tickern, 3s zwischen Batches (à 50)
- **644 Ticker in ~25 Minuten** pro Collector-Lauf
- **Graceful Error Handling:** Einzelne Ticker-Fehler werden geloggt, nie Abbruch
- **Schedule:** Nachtslot 01:00–03:00 MEZ (nach allen Daily-Jobs)

### Einschränkungen

- **Inoffizielle API**: Yahoo kann jederzeit Änderungen vornehmen
- **Rate-Limiting**: Aggressiv bei vielen Anfragen
- **Keine SLA** – produktiver Einsatz auf eigenes Risiko
- **Kein Batch-Endpoint** für `info` – jeder Ticker ist ein separater Call
- **`upgrades_downgrades` liefert keine Analyst-Namen** (nur Firm)

---

## 3. ARK Funds – Holdings via arkfunds.io API

**Kategorie:** Smart Money (aktiv gemanagte ETFs)
**API-Endpoint:** `https://arkfunds.io/api/v2/etf/holdings?symbol={ETF}`
**API-Doku:** https://arkfunds.io/api
**Status:** ✅ Implementiert (Sprint 2)

> **Hinweis:** Die direkte CSV-URL von ark-funds.com gibt 403 zurück (Cloudflare-Schutz).
> Stattdessen nutzen wir die kostenlose arkfunds.io JSON-API (Drittanbieter, nicht offiziell von ARK).

### Was es liefert

JSON-Response pro ETF mit Holdings-Array:

- `fund`: ETF-Ticker (ARKK, ARKQ, ...)
- `date`: Snapshot-Datum
- `ticker`: Aktien-Ticker
- `company`: Firmenname
- `cusip`: CUSIP-Identifier
- `shares`: Anzahl gehaltener Shares
- `market_value`: Marktwert in USD
- `share_price`: Aktienkurs
- `weight`: Gewichtung im ETF (%)
- `weight_rank`: Rang nach Gewichtung

### Aktuelle ETFs (Stand April 2026)

| ETF | Thema | Tracking-Priorität |
|---|---|---|
| **ARKK** | Innovation Flagship | ⭐⭐⭐ Hoch |
| **ARKQ** | Autonomous Tech & Robotics | ⭐⭐⭐ Hoch |
| **ARKW** | Next Generation Internet | ⭐⭐⭐ Hoch |
| **ARKG** | Genomic Revolution | ⭐⭐ Mittel |
| **ARKF** | Fintech Innovation | ⭐⭐ Mittel |
| **ARKX** | Space & Defense Innovation | ⭐⭐⭐ Hoch |
| **PRNT** | 3D Printing | ⭐ Niedrig |
| **IZRL** | Israel Innovation | ⭐ Niedrig |
| **ARKVX** | Venture Fund | ⏸ Kein Trading |
| **ARKSX** | Alternative | prüfen |

### Frequenz

- **Täglich nach US-Börsenschluss** (ca. 22:00 MEZ / 16:00 ET)
- arkfunds.io Aggregation meist bis 23:00 MEZ abgeschlossen
- Collector läuft um 23:00 MEZ

### Bekannte Stolpersteine

- **Cash-Positionen** wie "GOLDMAN FS TRSY OBLIG INST 468" sind keine Aktien → werden per Regex gefiltert
- **Internationale Titel** wie Komatsu (KMTUY), BYD (BYDDY) haben OTC-Ticker → nur aufgenommen wenn bei Alpaca handelbar
- **Drittanbieter-Risiko:** arkfunds.io ist keine offizielle ARK-Quelle, könnte ausfallen

---

## 4. SEC EDGAR – Form 4 (Insider-Trades)

**Kategorie:** Insider-Transaktionen (Pflichtmeldung)
**Basis-URL:** https://www.sec.gov/cgi-bin/browse-edgar
**Moderne API:** https://data.sec.gov/submissions/CIK{CIK}.json
**Status:** ✅ Implementiert (Sprint 3)

### Was es liefert

Jeder Kauf oder Verkauf durch einen "Insider" (CEO, CFO, Direktoren, >10%-Aktionäre) der eigenen Firmenaktie.

**Felder:**
- Insider-Name und Position
- Transaktionsdatum und Filing-Datum
- Transaktionstyp (P=Purchase, S=Sale, ...)
- Shares und Preis
- Shares nach Transaktion

### Frequenz & Verzögerung

- **Gesetzlich: Innerhalb von 2 Geschäftstagen** nach Transaktion
- In der Praxis oft am letzten erlaubten Tag
- Für dein Signal: **Fast Echtzeit** (max. 2 Tage Delay)

### API-Zugang

SEC verlangt einen **aussagekräftigen User-Agent** mit Kontakt:

```python
headers = {
    "User-Agent": "Sebastian Trading Signals research@example.com"
}
```

**Rate-Limit:** Max. 10 Requests pro Sekunde.

### Datenformat

- Form 4 ist XML-basiert (Format 4.0)
- Moderne JSON-APIs verfügbar unter `data.sec.gov`
- Alternativ: XBRL-Feeds für maschinenlesbare Aggregation

### Wichtige Konzepte

- **CIK (Central Index Key)**: SEC-interne Firmen-ID, wird zum Ticker gemappt
- **Form 4 vs. Form 144**: Form 144 ist Absicht zum Verkauf, Form 4 ist die tatsächliche Transaktion
- **Nicht-offene-Markt-Transaktionen ignorieren**: Zuteilungen, Optionen-Exercises, Schenkungen sind kein Signal

### Signalwert

- **Insider-Käufe**: Starkes Signal (CEOs kaufen mit eigenem Geld nur bei Überzeugung)
- **Insider-Verkäufe**: Schwaches Signal (geplante Programme, Steuern, Diversifikation)
- **Cluster-Käufe**: Mehrere Insider kaufen in kurzer Zeit → sehr starkes Signal

### Implementierungsdetails (Sprint 3)

- **Collector:** `Form4Collector` (Universe-driven, 644 Ticker)
- **Client:** `SECClient` mit Rate Limiting (10 req/s) und CIK-Mapping
- **Parsing:** `xml.etree.ElementTree` (stdlib, keine externe Dependency)
- **Dedup:** Unique Constraint auf `(cik, insider_name, transaction_date, transaction_type, shares, price_per_share)`
- **Derived:** `InsiderClusterComputer` erkennt Cluster-Käufe (≥2 Insider in 21 Tagen)
- **Schedule:** Täglich 23:30 MEZ

---

## 5. SEC EDGAR – Form 13F (Institutionelle Holdings)

**Kategorie:** Quartalsberichte großer Fonds (>100 Mio. $ AUM)
**Basis-URL:** https://www.sec.gov/cgi-bin/browse-edgar
**Moderne API:** https://data.sec.gov
**Status:** ✅ Implementiert (Sprint 3)

### Was es liefert

Vollständige Holding-Listen aller Fonds mit mindestens 100 Mio. $ verwaltetem Vermögen, **quartalsweise**.

### Verzögerung

- **Bis zu 45 Tage nach Quartalsende** – nicht für taktisches Trading geeignet
- Gut für **Kontext**: Welche Aktien haben Berkshire, Bridgewater, Soros Fund?

### Nutzung im Projekt

- Nicht als Signal für Einzeltrades
- Aber als **Feature**: "In wie vielen Top-13F-Holdern ist dieser Titel?"
- Historische Analyse: Wer hat früh bei Nvidia investiert?

### Top-Filer, die interessant sind (Beispiele)

- Berkshire Hathaway (Warren Buffett)
- Scion Capital (Michael Burry)
- Pershing Square (Bill Ackman)
- Tiger Global
- Renaissance Technologies
- Bridgewater Associates

### Implementierungsdetails (Sprint 3)

- **Collector:** `Form13FCollector` (Filer-driven, Top-20 Institutionelle)
- **Top-Filer:** Buffett, Burry, Ackman, Renaissance, Tiger, Bridgewater, Citadel, Two Sigma, D.E. Shaw, Millennium, Point72, Greenlight, Baupost, Third Point, Icahn, Elliott, Duquesne, Coatue, Appaloosa, ARK
- **Parsing:** 13F infotable XML mit Namespace-Handling
- **Dedup:** Unique Constraint auf `(filer_cik, report_period, cusip)`
- **Schedule:** Wöchentlich Sonntag 10:00 MEZ

---

## 6. Senate eFD – Politiker-Trades (Congress)

**URL:** https://efdsearch.senate.gov/search/
**Kategorie:** US-Senator-Trades (STOCK Act Compliance)
**Status:** ✅ Implementiert (Sprint 4)

### Was es liefert

Alle Periodic Transaction Reports (PTRs) von US-Senatoren – offizielle Finanz-Disclosure gemäß STOCK Act. Enthält:

- **Politician Name** (Senator)
- **Ticker/Asset** (Aktien, manchmal ETFs)
- **Transaction Type** (Purchase, Sale, Exchange)
- **Transaction Date** + **Disclosure Date**
- **Amount Range** (z.B. "$1,001 - $15,000")
- **Owner** (Self, Spouse, Joint, Child)
- **Comment** (optional)

### Zugang & Kosten

- **Kostenlos** – Offizielle US-Regierungsquelle
- **Kein API-Token nötig** – HTML-Scraping des Suchformulars
- **Session-basiert** – Erfordert Terms Agreement + CSRF-Token
- **Rate Limiting:** Konservativ 2 req/s (keine offiziellen Limits dokumentiert)

### Implementierung

- **Client:** `DisclosureClient` mit `requests.Session()` + BeautifulSoup
- **Collector:** `PoliticianTradesCollector(BaseCollector)` – Template-Method-Pattern
- **Tabelle:** `signals.politician_trades` mit Dedup via Unique Constraint
- **Schedule:** Wöchentlich Sonntag 11:00 MEZ
- **Lookback:** 365 Tage (fängt verzögerte Meldungen ab)

### Einschränkungen

- **Verzögerung: 30–45 Tage** (STOCK Act erlaubt bis zu 45 Tage Meldefrist)
- **Amount-Ranges statt exakter Beträge** ("$1,001 – $15,000")
- **Nur Senate** – House PTRs sind PDF-only (zukünftiges Enhancement)
- **HTML-Scraping fragil** – Strukturänderungen können den Parser brechen
- **Keine Party/State-Info** aus der Suchseite (ggf. über Merge mit Bioguide)

### Verworfene Alternativen

- ~~**Capitol Trades Scraping**~~ → ToS-Grauzone, keine offizielle API
- ~~**Quiver Quantitative API**~~ → 30 $/Monat, Constraint: kostenlos bleiben
- ~~**Stock Watcher S3**~~ → Community-Projekt, S3-Bucket teils gesperrt (403)

### Realistische Einschätzung

Aufgrund der Verzögerung wahrscheinlich kein starkes Alpha-Signal, aber als Feature im Aggregat sinnvoll ("Hat X oder Y Politiker diese Aktie gekauft?").

---

## 7. Noch nicht implementiert – Ideen für später

### OpenInsider (Alternative zu SEC EDGAR)
- URL: http://openinsider.com
- Aggregiert Form-4-Daten mit schönem UI
- Kostenlose Scraping-Möglichkeit
- Vorteil: Bereits vorgefilterte Cluster-Käufe

### Finviz (Screener + News)
- URL: https://finviz.com
- Insider-Screener, News-Aggregation
- Kostenlose Nutzung für persönlichen Gebrauch
- Scraping in Grauzone

### Unusual Whales (Options Flow + Politiker + Insider)
- URL: https://unusualwhales.com
- Kommerziell, ~40 $/Monat
- Sehr umfassend, aber teuer
- Erst evaluieren, wenn kostenlose Quellen nicht reichen

### Nancy Pelosi Tracker (Twitter Bots)
- Diverse Bots auf X/Twitter, die Pelosi-Trades tracken
- Nicht zuverlässig, aber interessant als Sentiment-Signal

### News-Sentiment-APIs
- NewsAPI (kostenlos begrenzt)
- Alpha Vantage News & Sentiment
- Eigene RSS-Feed-Aggregation

### On-Chain-Daten (Crypto, später)
- Arkham Intelligence
- Nansen
- Whale Alert
- Etherscan API direkt

---

## Datenqualitäts-Prüfungen

Jeder Collector sollte automatisch prüfen:

1. **Vollständigkeit:** Wurden alle erwarteten Titel zurückgegeben?
2. **Plausibilität:** Sind Preise im sinnvollen Bereich (z.B. nicht negativ, nicht > 10x Vortag)?
3. **Aktualität:** Ist das Snapshot-Datum heutig?
4. **Konsistenz:** Summiert sich ein ARK-ETF auf ~100% Gewichtung?
5. **Duplikate:** Sind wir sicher, dass wir nichts doppelt speichern?

Fehler werden in `collection_log` protokolliert und bei kritischen Ausfällen per Telegram-Notification gemeldet.

---

## Hinweis zu Terms of Service

Bei allen Scraping-Quellen (ARK, OpenInsider, Finviz) muss vor der Implementierung geprüft werden:
- Ist Scraping in den ToS erlaubt?
- Gibt es robots.txt-Einschränkungen?
- Ist der Use Case (privates Forschungsprojekt) gedeckt?

Offizielle APIs und Regierungsportale (SEC EDGAR, Senate eFD, yfinance) sind unproblematisch, sofern die dokumentierten Rate-Limits eingehalten werden.
