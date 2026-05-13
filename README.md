# Alpaca-Broker – Signal Warehouse

> A private, experimental **Signal Warehouse** that systematically collects "smart money" signals and market data to develop data-driven trading strategies.

## Mission

Build a data-driven research platform that aggregates signals from multiple sources (ARK Invest, SEC Form 4/13F, US politician trades, technical indicators, analyst ratings, news sentiment) into a unified feature store, enabling informed trading strategy development on Alpaca Paper Trading.

## Key Features

- **📊 7 Data Sources:** Alpaca (prices + news), ARK (smart money), SEC EDGAR (insiders + institutions), Senate eFD (politicians), yfinance (fundamentals + ratings + earnings), FinBERT (sentiment)
- **🖥️ Dashboard:** FastAPI + React SPA with 7 pages (Dashboard, Universe, Signals, Features, Logs, Settings, Ticker Detail)
- **📈 674+ Active Tickers:** S&P 500 + Nasdaq 100 + ARK expansions
- **🗄️ PostgreSQL 18:** Append-only raw data layer + recomputable derived features + feature store
- **🤖 Automated:** APScheduler with 15 scheduled jobs (9 daily, 4 weekly, 1 monthly, 1 maintenance)
- **🧠 Feature Pipeline:** Daily ML feature vectors (55 features × 9 signal groups) with target backfill
- **📰 News Sentiment:** Alpaca News API + ProsusAI/finbert for headline-based sentiment scoring
- **🔒 Paper Only:** Hardcoded safety check – **never** live trading

## Technology Stack

| Component | Technology |
|---|---|
| Language | Python 3.12+ |
| Backend | FastAPI |
| Frontend | Vite + React (SPA) |
| Database | PostgreSQL 18 (SQLAlchemy 2.0 + Alembic) |
| Scheduler | APScheduler |
| NLP | ProsusAI/finbert (110M params, local CPU) |
| Package Management | uv (Python), npm (Frontend) |
| Deployment | Docker on Unraid |

## Current Status

- **Sprint 8c completed** – News Sentiment Pipeline + UI integration live
- **System running** on Unraid (192.168.1.93:8090)
- **Next:** Waiting Phase (2–3 months data collection), then Sprint 9 (Exploratory Analysis)

## Documentation

All project documentation lives in the [`docs/`](docs/) folder. Start with:

- **[docs/INDEX.md](docs/INDEX.md)** – Documentation navigator (find the right doc for your question)
- **[CLAUDE.md](CLAUDE.md)** – AI session entry point

---

*This is a private research project. Not financial advice. No warranty.*
