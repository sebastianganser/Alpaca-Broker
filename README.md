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

## Datenquellen

- **Marktdaten:** Alpaca Market Data API (OHLCV, IEX feed) – *yfinance als Fallback* ✅
- **Smart Money:** ARK Invest ETF Holdings via arkfunds.io API (täglich) ✅
- **Universe:** S&P 500 + Nasdaq 100 + ARK-Ergänzungen (644 aktive Ticker) ✅
- **Insider-Trades:** SEC EDGAR Form 4 ✅
- **Institutionelle:** SEC EDGAR Form 13F ✅
- **Politiker:** Senate eFD (efdsearch.senate.gov, kostenlos) ✅
- **Fundamentals:** yfinance (P/E, Margins, Revenue Growth, EPS, Beta, etc.) ✅
- **Analyst-Ratings:** yfinance (Upgrades/Downgrades) ✅
- **Earnings-Kalender:** yfinance (EPS-Estimates, Surprises) ✅
- **Technische Indikatoren:** pandas-ta (SMA, EMA, RSI, MACD, Bollinger, ATR, Volume SMA, RS vs. SPY) ✅

## Technologie-Stack

- Python 3.12+
- uv (Paketmanager)
- PostgreSQL 18
- SQLAlchemy 2.0 + Alembic
- Pydantic Settings
- APScheduler
- Alpaca API (Market Data + Paper Trading)
- FastAPI (Backend-API + SPA Host)
- Vite + React (Dashboard UI)
- Docker Compose (Unraid)

## Dokumentation

Die vollständige Dokumentation liegt im Projekt selbst:

- [`CLAUDE.md`](CLAUDE.md) – Projekt-Einstiegspunkt
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) – Technische Architektur
- [`docs/ROADMAP.md`](docs/ROADMAP.md) – Sprint-Planung
- [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) – Datenquellen-Katalog
- [`docs/DECISIONS.md`](docs/DECISIONS.md) – Decision Log
- [`docs/LEARNINGS.md`](docs/LEARNINGS.md) – Erkenntnisse aus den Daten

## Status

🟢 **Produktionsbetrieb** – Deployed auf Unraid (192.168.1.93:8090). Dashboard & Operations UI (Sprint 7) live. 10 Scheduler-Jobs aktiv (5 täglich, 4 wöchentlich, 1 monatlich). 644 Ticker, ~820k Preisdatensätze, ~818k TA-Indikatoren. 303 Tests.
Nächster Schritt: Sprint 8 (Feature Pipeline).

## Lizenz

Privates Projekt, keine öffentliche Lizenz.
