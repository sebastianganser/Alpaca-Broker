# Alpaca-Broker

> Privates Research- und Lernprojekt: Aufbau eines Signal Warehouse für quantitative Trading-Signale mit anschließender Strategie-Entwicklung auf Alpaca Paper Trading.

## ⚠️ Disclaimer

Dies ist **keine Finanzberatung** und **kein produktives Trading-System**. Alle Strategien laufen ausschließlich im Paper-Trading-Modus ohne echtes Geld. Das Projekt dient dem Lernen, Forschen und Experimentieren mit quantitativen Methoden und AI-gestützter Datenanalyse.

## Projektziel

Statt blind populäre Trading-Strategien zu kopieren (Copy Trading, Wheel-Strategie, Trailing Stops), bauen wir zuerst ein robustes **Datenfundament** auf. Über mehrere Monate sammeln wir möglichst viele Signale aus öffentlichen Quellen und entwickeln dann **datenbasiert** eigene Strategien.

## Architektur (Kurzfassung)

```
Datenquellen → Collectors → PostgreSQL (Raw) → Derived Layer → Feature Store
                                                                    ↓
                                                             Analyse/Scoring
                                                                    ↓
                                                          Paper Trading (später)
```

## Datenquellen (geplant)

- **Marktdaten:** Alpaca Market Data API (OHLCV, IEX feed) – *yfinance als Fallback*
- **Smart Money:** ARK Invest ETF Holdings via arkfunds.io API (täglich)
- **Universe:** S&P 500 + Nasdaq 100 + ARK-Ergänzungen (644 aktive Ticker)
- **Insider-Trades:** SEC EDGAR Form 4
- **Institutionelle:** SEC EDGAR Form 13F
- **Politiker:** Quiver Quantitative API
- **Analyst-Ratings:** yfinance
- **Technische Indikatoren:** Berechnet aus OHLCV

## Technologie-Stack

- Python 3.12+
- uv (Paketmanager)
- PostgreSQL 18
- SQLAlchemy 2.0 + Alembic
- Pydantic Settings
- APScheduler
- Alpaca API (Market Data + Paper Trading)
- Docker Compose (Unraid)
- FastAPI (später)

## Dokumentation

Die vollständige Dokumentation liegt im Projekt selbst:

- [`CLAUDE.md`](CLAUDE.md) – Projekt-Einstiegspunkt
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) – Technische Architektur
- [`docs/ROADMAP.md`](docs/ROADMAP.md) – Sprint-Planung
- [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) – Datenquellen-Katalog
- [`docs/DECISIONS.md`](docs/DECISIONS.md) – Decision Log
- [`docs/LEARNINGS.md`](docs/LEARNINGS.md) – Erkenntnisse aus den Daten

## Status

🟢 **Sprint 2 + 1b abgeschlossen** – Alpaca Price Collector + ARK Holdings Tracker (644 Ticker, 8 ARK-ETFs, S&P 500 + Nasdaq 100).
Nächster Schritt: Sprint 3 (SEC Insider Trades).

## Lizenz

Privates Projekt, keine öffentliche Lizenz.
