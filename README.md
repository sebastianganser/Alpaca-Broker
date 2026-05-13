# Alpaca-Broker – Signal Warehouse

> A private, experimental **Signal Warehouse** that systematically collects "smart money" signals and market data to develop data-driven trading strategies.

## Mission

Build a data-driven research platform that aggregates signals from multiple sources (ARK Invest, SEC Form 4/13F, US politician trades, technical indicators, analyst ratings) into a unified feature store, enabling informed trading strategy development on Alpaca Paper Trading.

## Key Features

- **📊 6 Data Sources:** Alpaca (prices), ARK (smart money), SEC EDGAR (insiders + institutions), Senate eFD (politicians), yfinance (fundamentals + ratings + earnings)
- **🖥️ Dashboard:** FastAPI + React SPA with 7 pages (Dashboard, Universe, Signals, Features, Logs, Settings, Ticker Detail)
- **📈 674+ Active Tickers:** S&P 500 + Nasdaq 100 + ARK expansions
- **🗄️ PostgreSQL 18:** Append-only raw data layer + recomputable derived features + feature store
- **🤖 Automated:** APScheduler with 12 scheduled jobs (7 daily, 4 weekly, 1 monthly)
- **🧠 Feature Pipeline:** Daily ML feature vectors (49 features × 8 signal groups) with target backfill
- **🔒 Paper Only:** Hardcoded safety check – **never** live trading

## Technology Stack

| Component | Technology |
|---|---|
| Language | Python 3.12+ |
| Backend | FastAPI |
| Frontend | Vite + React (SPA) |
| Database | PostgreSQL 18 (SQLAlchemy 2.0 + Alembic) |
| Scheduler | APScheduler |
| Package Management | uv (Python), npm (Frontend) |
| Deployment | Docker on Unraid |

## Current Status

- **Sprint 8 completed** – Feature Pipeline + Features Page live
- **System running** on Unraid (192.168.1.93:8090)
- **Next:** Waiting Phase (2–3 months data collection), then Sprint 9 (Exploratory Analysis)

## Documentation

All project documentation lives in the [`docs/`](docs/) folder. Start with:

- **[docs/INDEX.md](docs/INDEX.md)** – Documentation navigator (find the right doc for your question)
- **[CLAUDE.md](CLAUDE.md)** – AI session entry point

---

*This is a private research project. Not financial advice. No warranty.*
