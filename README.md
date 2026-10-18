# Alpaca-Broker – Signal Warehouse

> A private, experimental **Signal Warehouse** that systematically collects "smart money" signals and market data to develop data-driven trading strategies.

## Mission

Build a data-driven research platform that aggregates signals from multiple sources (ARK Invest, SEC Form 4/13F, US politician trades, technical indicators, analyst ratings, news sentiment) into a unified feature store, enabling informed trading strategy development on Alpaca Paper Trading.

## Key Features

- **📊 Data Sources:** Alpaca (prices SIP + news), ARK (smart money), SEC EDGAR (insiders + institutions), Senate eFD (politicians), yfinance (fundamentals, ratings, earnings, estimates, options IV), FRED (macro), Massive API (short volume + FINRA short interest), FinBERT (sentiment)
- **🖥️ Dashboard:** FastAPI + React SPA with 7 pages (Dashboard, Universe, Signals, Features, Logs, Settings, Ticker Detail)
- **📈 760 Active Tickers** (17 Oct 2026, after the S&P 500 survivorship repair): S&P 500 + Nasdaq 100 + ARK holdings + benchmark/sector ETFs, point-in-time universe for features
- **🗄️ PostgreSQL 18:** raw data layer + recomputable derived features + feature store, rolling 20-quarter retention
- **🤖 Automated:** APScheduler with 29 jobs (14 daily incl. the nightly chain, 1 weekday seal job in New York time, 8 weekly, 2 monthly, 4 chain steps)
- **🧠 Feature Pipeline:** Daily feature vectors (~97 features in 17 groups) with forward-return and barrier-label backfill, daily Context Pack for the analysis skill
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

- **Sprint 9.5c** (Candidate Pipeline MVP), Phase 2 – short-term research K1–K4 done (no validated edge yet), forecast pipeline (`forecasts.yaml` ingest, pre-registered evaluation) implemented, deploy pending
- **System running** on Unraid (192.168.1.93:8090)
- **Next:** Sprint 10 (Signal Scoring) planned from mid-December 2026 – see [docs/ROADMAP.md](docs/ROADMAP.md)

## Documentation

All project documentation lives in the [`docs/`](docs/) folder. Start with:

- **[docs/INDEX.md](docs/INDEX.md)** – Documentation navigator (find the right doc for your question)
- **[CLAUDE.md](CLAUDE.md)** – AI session entry point

---

*This is a private research project. Not financial advice. No warranty.*
