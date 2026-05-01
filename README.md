# Alpaca-Broker – Signal Warehouse

> A private, experimental **Signal Warehouse** that systematically collects "smart money" signals and market data to develop data-driven trading strategies.

## Mission

Build a data-driven research platform that aggregates signals from multiple sources (ARK Invest, SEC Form 4/13F, US politician trades, technical indicators, analyst ratings) into a unified feature store, enabling informed trading strategy development on Alpaca Paper Trading.

## Key Features

- **📊 6 Data Sources:** Alpaca (prices), ARK (smart money), SEC EDGAR (insiders + institutions), Senate eFD (politicians), yfinance (fundamentals + ratings + earnings)
- **🖥️ Dashboard:** FastAPI + React SPA for real-time monitoring, data exploration, and system management
- **📈 671+ Active Tickers:** S&P 500 + Nasdaq 100 + ARK expansions
- **🗄️ PostgreSQL 18:** Append-only raw data layer + recomputable derived features
- **🤖 Automated:** APScheduler with 10 scheduled jobs (daily, weekly, monthly)
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

- **Sprint 7 completed** – Dashboard & Operations UI live
- **System running** on Unraid (192.168.1.93:8090)
- **Next:** Sprint 8 (Feature Pipeline)

## Documentation

All project documentation lives in the [`docs/`](docs/) folder. Start with:

- **[docs/INDEX.md](docs/INDEX.md)** – Documentation navigator (find the right doc for your question)
- **[CLAUDE.md](CLAUDE.md)** – AI session entry point

---

*This is a private research project. Not financial advice. No warranty.*
