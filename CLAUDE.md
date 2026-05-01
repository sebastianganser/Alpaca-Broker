# CLAUDE.md – Alpaca-Broker Project

> **Entry point for every Claude session on this project.**
> Read this document first, then consult the linked detail documents as needed.
> For a complete document index, see [docs/INDEX.md](docs/INDEX.md).

---

## Project Identity

**Project name:** `Alpaca-Broker` (Python package: `trading-signals`)
**Owner:** Sebastian
**Started:** April 2026
**Repository:** [github.com/sebastianganser/Alpaca-Broker](https://github.com/sebastianganser/Alpaca-Broker) (private)
**Local project root:** `D:\Sebastian\Dokumente\Privat\Rudi\Coding\Workspaces\Alpaca-Broker`
**Deployment target:** Unraid server (Docker), separate PostgreSQL 18 (`postgresql18-alpaca`, port 5435)

---

## Project Mission (One Sentence)

> Build a **Signal Warehouse** that collects as many relevant market data points and "smart money" signals as possible daily, to later develop data-driven, robust trading strategies in Alpaca Paper Trading.

## What This Project Is NOT (Important!)

- ❌ **Not a live trading system** – everything runs exclusively in the Alpaca **paper trading** account
- ❌ **Not financial advice, no alpha guarantee** – this is a learning and research project
- ❌ **Not a "Claude trades autonomously" system** – LLMs are only used where natural language adds real value
- ❌ **Not crypto trading** (at least not in phase 1)
- ❌ **No options strategies initially** – wheel strategy deliberately deferred in favor of solid data foundation

---

## Three Core Principles

### 1. Collect Data Before Using Data
We do **not** start with a trading strategy. We start with a data collector that spends at least 2–3 months **without trading**, only writing data to the database. Only when we have enough material do we start distilling signals.

### 2. Separation of Raw Data and Evaluation
Raw data is sacred and never modified (append-only). Evaluations, scores, and signals are **computed** from raw data and can be recomputed any time if the algorithm changes.

### 3. Deterministic Core, LLM Only at the Edges
Critical paths (data fetching, computations, later order execution) are pure Python code with unit tests. LLMs are only used for unstructured tasks (news parsing, report generation, ad-hoc analyses).

---

## Current Status

**Phase:** 🟢 Sprint 7 completed + production operation
**Current sprint:** Operational – system running on Unraid, data collection active
**Next step:** Sprint 8 (Feature Pipeline)
**Last updated:** May 2026
**Deployment:** ✅ Unraid Docker (192.168.1.93:8090)

See [ROADMAP.md](docs/ROADMAP.md) for detailed progress.

---

## Quick Facts

| Property | Value |
|---|---|
| **Language** | Python 3.12+ |
| **Backend API** | FastAPI (same process as APScheduler) |
| **Frontend** | Vite + React SPA (Stitch "Precision Architect" Design) |
| **ORM** | SQLAlchemy 2.0 |
| **Migrations** | Alembic |
| **Database** | PostgreSQL 18 (`postgresql18-alpaca`, 192.168.1.93:5435, DB: `broker_data`, schema: `signals`) |
| **Package managers** | uv (Python), npm (frontend) |
| **Scheduler** | APScheduler (10 jobs: 5 daily, 4 weekly, 1 monthly) |
| **Price data** | Alpaca Market Data API (IEX feed, multi-symbol batch) |
| **Universe** | ~671 active tickers (S&P 500 + Nasdaq 100 + ARK + benchmarks, ETFs filtered via blacklist) |
| **Deployment** | Docker Compose on Unraid (1 container: Collector + API + UI, port 8090) |
| **Broker (later)** | Alpaca Paper Trading (NEVER live!) |
| **Version control** | Git, [GitHub](https://github.com/sebastianganser/Alpaca-Broker) |

---

## Model Routing (for LLM Tasks)

| Task | Model | Where |
|---|---|---|
| Architecture design, edge case analysis | Opus 4.6 | Claude Desktop (sparingly!) |
| Standard implementation, debugging | Sonnet 4.6 | Claude Desktop (default) |
| News parsing, daily reports | Haiku 4.5 | API (scheduler jobs) |
| Routine scheduler (price checks etc.) | **No LLM** | Python code |

**Expected cost in full operation:** ~€20/month Claude Pro + ~$10–15/month API costs

---

## Project Documents

| Document | Purpose | Update Frequency |
|---|---|---|
| **CLAUDE.md** (this document) | Entry point, project identity | Rarely |
| [docs/INDEX.md](docs/INDEX.md) | **Documentation navigator** – find the right doc | Rarely |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Deployment, project structure, data flow | On structural changes |
| [docs/DATABASE.md](docs/DATABASE.md) | Complete DB schema (all tables + DDL) | On schema changes |
| [docs/SCHEDULER.md](docs/SCHEDULER.md) | Execution schedule (all jobs) | When jobs change |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Data source catalog | When new sources added |
| [docs/DECISIONS_ARCHITECTURE.md](docs/DECISIONS_ARCHITECTURE.md) | Decisions: infrastructure & architecture | On arch decisions |
| [docs/DECISIONS_DATA.md](docs/DECISIONS_DATA.md) | Decisions: data sources & collectors | On data decisions |
| [docs/DECISIONS_FEATURES.md](docs/DECISIONS_FEATURES.md) | Decisions: features, scoring, trading | On strategy decisions |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Sprint planning, status | **After every sprint** |
| [docs/SESSION_LOG.md](docs/SESSION_LOG.md) | Chronological session history | **After every session** |
| [docs/LEARNINGS.md](docs/LEARNINGS.md) | Observed findings, debugging stories | Continuously |
| [docs/LEARNINGS_HYPOTHESES.md](docs/LEARNINGS_HYPOTHESES.md) | Hypotheses to test, planned investigations | As research evolves |

---

## Session Start Checklist for Claude

When starting a new session on this project:

1. ✅ **Read CLAUDE.md completely** (this document)
2. ✅ **Read ROADMAP.md** – where are we? Which sprint is active?
3. ✅ **Scan DECISIONS_*.md** – any recent decisions relevant to the current context?
4. ✅ **Read the relevant chapter** in ARCHITECTURE.md / DATABASE.md for the current sprint
5. ✅ **Ask Sebastian** what the goal of this session is before starting

## Session End Checklist for Claude

At the end of every productive session:

1. ✅ **Update ROADMAP.md** – what was done, what's the next step?
2. ✅ **Update SESSION_LOG.md** – document what happened
3. ✅ **New decisions in DECISIONS_*.md** (in the appropriate file)
4. ✅ **New findings in LEARNINGS.md** (if applicable)
5. ✅ **Update DATABASE.md** if schema changed
6. ✅ **Update SCHEDULER.md** if jobs changed
7. ✅ **Update ARCHITECTURE.md** if structure changed

---

## Important Boundaries & Safety Rules

### 🚨 Never Without Explicit Confirmation

- **Never** activate live trading (hardcoded check on `paper-api.alpaca.markets`)
- **Never** place real orders without manual approval
- **Never** commit credentials to Git (`.env` files in `.gitignore`)
- **Never** touch the GynOrg database (strict separation)

### Responsibility

This project is **private and experimental**. All decisions are made by Sebastian. Claude assists, implements, and advises – but final responsibility for every trade and configuration lies with the human.

---

## Relationship to Other Projects

Sebastian works on other projects in parallel. This project is **strictly separated** from:
- **GynOrg** (gynecological clinic management, own PostgreSQL)
- **WoSZ** (Wardens of Sector Zero, strategy game concept)
- **Other coding experiments**

Trading Signals has its own folder, its own DB, its own Docker Compose.
