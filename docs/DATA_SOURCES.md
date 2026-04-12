# DATA_SOURCES.md – Katalog der Datenquellen

> Detaillierte Dokumentation jeder Datenquelle: wie man sie abruft, was sie liefert, welche Einschränkungen sie hat.
> Wird erweitert, sobald neue Quellen hinzukommen.

---

## Übersicht

| Quelle | Kategorie | Kosten | Frequenz | Verzögerung | Sprint |
|---|---|---|---|---|---|
| **yfinance** | Preise, Fundamentals | Kostenlos | Täglich | ~20 Min. EOD | 1, 5 |
| **Alpaca Market Data** | Preise (Alternative) | Kostenlos mit Account | Echtzeit (Paper) | Sekunden | Backup |
| **ARK Funds CSVs** | Smart Money | Kostenlos | Täglich EOD | ~1 Tag | 2 |
| **SEC EDGAR – Form 4** | Insider-Trades | Kostenlos | Rolling | 2 Werktage (gesetzlich) | 3 |
| **SEC EDGAR – Form 13F** | Institutionelle Holdings | Kostenlos | Quartalsweise | Bis 45 Tage | 3 |
| **Capitol Trades** | Politiker-Trades | Kostenlos (Scraping) | Rolling | 30–45 Tage | – (ersetzt) |
| **Quiver Quantitative** | Politiker-Trades | Freemium (API) | Rolling | 30–45 Tage | 4 |
| **yfinance – Fundamentals** | P/E, Revenue, EPS | Kostenlos | Unregelmäßig | – | 5 |
| **yfinance – Ratings** | Analyst-Empfehlungen | Kostenlos | Rolling | – | 5 |

---

## 1. yfinance (Yahoo Finance)

**Kategorie:** Marktdaten (Preise, Fundamentals, Ratings)
**Python-Paket:** `yfinance`
**Offizielle Doku:** https://github.com/ranaroussi/yfinance

### Was es liefert

- **Preise:** Tägliche OHLCV-Daten für praktisch alle gelisteten Aktien weltweit
- **Fundamentals:** P/E, P/S, Market Cap, Revenue, EPS, Margins, etc.
- **Analyst-Ratings:** Konsens-Bewertungen und Kursziele
- **Earnings-Kalender:** Termine und historische Earnings
- **Dividenden, Splits, Actions**

### Beispiel-Code

```python
import yfinance as yf

# Preise abrufen
ticker = yf.Ticker("AAPL")
hist = ticker.history(period="1y", interval="1d")

# Fundamentals
info = ticker.info
pe_ratio = info.get("trailingPE")
market_cap = info.get("marketCap")

# Analyst-Ratings
recommendations = ticker.recommendations
```

### Einschränkungen

- **Inoffizielle API**: Yahoo kann jederzeit Änderungen vornehmen, die den Scraper brechen
- **Rate-Limiting**: Yahoo drosselt aggressiv bei vielen Anfragen → Batch-Modus nutzen (`yf.download(tickers_list)`)
- **Datenqualität**: Meist gut, aber gelegentliche Fehler (besonders bei Small Caps)
- **Keine Intraday-Daten für kostenlose Nutzung** – nur EOD
- **Keine SLA** – produktiver Einsatz auf eigenes Risiko

### Best Practices

- Batch-Downloads verwenden: `yf.download(["AAPL", "MSFT", ...], period="5d")`
- Retries mit exponentiellem Backoff
- Periodisch prüfen, ob das Paket noch aktuelle Daten liefert
- Als Fallback Alpaca Market Data vorhalten

---

## 2. Alpaca Market Data (Backup)

**Kategorie:** Marktdaten (Backup zu yfinance)
**Python-Paket:** `alpaca-py`
**Doku:** https://alpaca.markets/docs/market-data/

### Was es liefert

- Echtzeit- und historische Kursdaten (US-Märkte)
- Trades, Quotes, Bars in verschiedenen Intervallen
- Begrenzt auf US-Titel

### Warum als Backup

- Benötigt API-Key (hat Sebastian eh für Paper Trading)
- Offizielle API mit SLA
- Aber: teilweise Latenz oder Einschränkungen im Free-Tier
- yfinance ist breiter (inkl. europäische Titel)

### Nutzung

Erst einsetzen, wenn yfinance mal ausfällt oder Spezialdaten nötig werden.

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

---

## 5. SEC EDGAR – Form 13F (Institutionelle Holdings)

**Kategorie:** Quartalsberichte großer Fonds (>100 Mio. $ AUM)
**Basis-URL:** https://www.sec.gov/cgi-bin/browse-edgar
**Moderne API:** https://data.sec.gov

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

---

## 6. Capitol Trades (Politiker-Trades)

**URL:** https://www.capitoltrades.com
**Kategorie:** US-Politiker-Trades (STOCK Act Compliance)

### Was es liefert

Alle gemeldeten Aktien-Transaktionen von US-Kongressmitgliedern und Senatoren.

### Einschränkungen

- **Verzögerung: 30–45 Tage** (Meldepflicht, oft am letzten Tag eingereicht)
- **Amount-Ranges statt exakter Beträge** ("$1,001 – $15,000")
- **Ehepartner-Trades** werden teilweise intransparent gehandhabt

### Scraping vs. API

Capitol Trades hat **keine offizielle API**. Scraping ist:
- Technisch möglich (HTML-Parsing)
- Rechtlich in Grauzone (Terms of Service prüfen!)
- Alternative: **Quiver Quantitative** hat eine offizielle API mit Freemium-Tier

### Empfehlung

- ~~**Option A:** Scraping von Capitol Trades~~ → Verworfen (fragile Infrastruktur, ToS-Grauzone)
- **Option B: Quiver Quantitative API** → **Gewählt** (offizielle API, saubere JSON-Responses, Free Tier verfügbar)
- Siehe [DECISIONS.md](DECISIONS.md), Eintrag vom 2026-04-12: "Politiker-Trades-Quelle → Quiver Quantitative API"

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

Bei allen Scraping-Quellen (ARK, Capitol Trades, OpenInsider, Finviz) muss vor der Implementierung geprüft werden:
- Ist Scraping in den ToS erlaubt?
- Gibt es robots.txt-Einschränkungen?
- Ist der Use Case (privates Forschungsprojekt) gedeckt?

Offizielle APIs (SEC EDGAR, yfinance) sind unproblematisch, sofern die dokumentierten Rate-Limits eingehalten werden.
