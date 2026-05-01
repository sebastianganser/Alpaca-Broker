# ARCHITECTURE.md – Technical Architecture

> System architecture, deployment topology, project structure, and data flow.
> For database schema details, see [DATABASE.md](DATABASE.md).
> For execution schedule, see [SCHEDULER.md](SCHEDULER.md).
>
> See also: [INDEX.md](INDEX.md)

**Last updated:** May 2026

---

## Deployment Topology

```
┌─────────────────────────────────────────────────────────────┐
│  Windows Machine (Development + Frontend)                   │
│  ├─ Claude Desktop (Sonnet/Opus for design, debugging)      │
│  ├─ VS Code + Claude Code                                   │
│  └─ Git Client → GitHub                                     │
└────────────────────────┬────────────────────────────────────┘
                         │ SSH / Docker Remote
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Unraid Server (Production)                                 │
│  ✅ LIVE – Container running on 192.168.1.93:8090            │
│                                                             │
│  ┌───────────────────┐    ┌────────────────────────────┐    │
│  │ signal-collector  │    │ postgresql18-alpaca        │    │
│  │ (Python Container)│───▶│ (separate container)       │    │
│  │                   │    │ DB: broker_data            │    │
│  │ APScheduler +     │    │ Schema: signals            │    │
│  │ FastAPI + React   │    │ User: sebastian            │    │
│  │ • Daily 22–00h    │    │ Port: 5435                 │    │
│  │ • Night 01–03h    │    │ Volume: /mnt/user/         │    │
│  │ • UI on :8090     │    │   Datafolder/Broker/       │    │
│  └───────────────────┘    └────────────────────────────┘    │
│                                                             │
│                           ┌────────────────────────────┐    │
│                           │ Alpaca API                 │    │
│                           │ (Paper Trading ONLY!)      │    │
│                           │ Endpoint hardcoded check   │    │
│                           └────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Network:** Collector container connects directly to the existing PostgreSQL instance via host IP.
**Backup:** Daily pg_dump via cron on Unraid to existing backup folder.
**Deployment gate:** ✅ Deployment completed. System running in production since 13 April 2026.

---

## Project Structure

```
Alpaca-Broker/
├── CLAUDE.md                      # Project entry point
├── README.md                      # GitHub overview
├── docs/                          # All documentation
│   ├── INDEX.md                   # Documentation navigator
│   ├── ARCHITECTURE.md            # This document
│   ├── DATABASE.md                # DB schema reference
│   ├── SCHEDULER.md               # Execution schedule
│   ├── ROADMAP.md                 # Sprint planning
│   ├── SESSION_LOG.md             # Session history
│   ├── DATA_SOURCES.md            # Data source catalog
│   ├── DECISIONS_ARCHITECTURE.md  # Architecture decisions
│   ├── DECISIONS_DATA.md          # Data source decisions
│   ├── DECISIONS_FEATURES.md      # Feature/strategy decisions
│   ├── LEARNINGS.md               # Observed findings
│   └── LEARNINGS_HYPOTHESES.md    # Hypotheses to test
├── infra/                         # Deployment
│   ├── docker-compose.yml         # Collector service only (DB runs separately)
│   ├── Dockerfile.collector       # 3-stage build (Node+Python+Runtime)
│   └── entrypoint.sh              # DB wait + Alembic + CMD
├── src/
│   ├── trading_signals/           # Python package
│   │   ├── __init__.py
│   │   ├── config.py              # Pydantic Settings
│   │   ├── db/                    # Database layer
│   │   │   ├── base.py            # SQLAlchemy Base (schema: signals)
│   │   │   ├── session.py         # Engine + Session factory
│   │   │   └── models/            # ORM models per table
│   │   │       ├── universe.py    # ✅
│   │   │       ├── prices.py      # ✅ Sprint 1
│   │   │       ├── ark.py         # ✅ Sprint 2
│   │   │       ├── insider.py     # ✅ Sprint 3
│   │   │       ├── politicians.py # ✅ Sprint 4
│   │   │       ├── fundamentals.py# ✅ Sprint 5 (3 models)
│   │   │       ├── technical_indicators.py # ✅ Sprint 6
│   │   │       └── features.py    # Sprint 8
│   │   ├── collectors/            # Data collectors (one module per source)
│   │   │   ├── base.py            # ✅ Abstract BaseCollector
│   │   │   ├── prices_alpaca.py   # ✅ Sprint 1b (primary)
│   │   │   ├── prices_yfinance.py # ✅ Sprint 1 (fallback)
│   │   │   ├── ark_holdings.py    # ✅ Sprint 2
│   │   │   ├── gap_detector.py    # ✅ Sprint 1
│   │   │   ├── sec_client.py      # ✅ Sprint 3
│   │   │   ├── form4_collector.py # ✅ Sprint 3
│   │   │   ├── form13f_collector.py # ✅ Sprint 3
│   │   │   ├── disclosure_client.py # ✅ Sprint 4 (curl_cffi + AJAX)
│   │   │   ├── politician_trades_collector.py # ✅ Sprint 4
│   │   │   ├── yfinance_client.py   # ✅ Sprint 5 (shared client)
│   │   │   ├── fundamentals_collector.py # ✅ Sprint 5
│   │   │   ├── analyst_ratings_collector.py # ✅ Sprint 5
│   │   │   └── earnings_calendar_collector.py # ✅ Sprint 5
│   │   ├── derived/               # Computed features
│   │   │   ├── ark_deltas.py      # ✅ Sprint 2
│   │   │   ├── insider_clusters.py# ✅ Sprint 3
│   │   │   ├── technical_indicators.py # ✅ Sprint 6
│   │   │   └── feature_pipeline.py# Sprint 8
│   │   ├── universe/              # Dynamic ticker universe
│   │   │   ├── manager.py         # ✅
│   │   │   ├── alpaca_validator.py # ✅
│   │   │   ├── index_sync.py      # ✅ Sprint 1b
│   │   │   └── onboarder.py       # ✅ Auto-expansion + backfill
│   │   ├── api/                   # ✅ Sprint 7 (FastAPI backend)
│   │   │   ├── deps.py            # DB session + scheduler DI
│   │   │   ├── job_tracker.py     # APScheduler event listener
│   │   │   ├── schemas.py         # 21+ Pydantic response schemas
│   │   │   ├── tasks.py           # BackfillManager (threading)
│   │   │   └── routes/
│   │   │       ├── dashboard.py   # /api/v1/dashboard/summary
│   │   │       ├── universe.py    # /api/v1/universe (paginated)
│   │   │       ├── signals.py     # /api/v1/signals/ark,insider,...
│   │   │       ├── ticker.py      # /api/v1/ticker/{sym}/prices,...
│   │   │       └── operations.py  # /api/v1/ops/scheduler,backfill,db
│   │   ├── scheduler/
│   │   │   └── jobs.py            # ✅ 10 jobs configured
│   │   └── utils/
│   │       ├── logging.py         # ✅
│   │       └── retry.py           # ✅
│   └── alembic/                   # Database migrations (001-017)
├── tests/
│   ├── unit/                      # ✅ 303 tests
│   ├── integration/
│   └── fixtures/
├── scripts/                       # One-time scripts
├── frontend/                      # ✅ Sprint 7 (Vite + React SPA)
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts             # Proxy -> :8090 (dev)
│   └── src/
│       ├── main.tsx
│       ├── App.tsx                # Router + QueryClient
│       ├── Layout.tsx             # Sidebar (logo + world clock) + Outlet
│       ├── api.ts                 # Typed API client (fetch wrapper)
│       ├── index.css              # Precision Architect Design System
│       └── pages/
│           ├── DashboardPage.tsx  # Collector status, stats, health
│           ├── UniversePage.tsx   # Filtered/paginated ticker table
│           ├── SignalsPage.tsx    # Tabbed: ARK, Insider, Politicians, Analyst
│           ├── LogsPage.tsx       # Scheduler logs (tabbed: all / errors)
│           ├── SettingsPage.tsx   # Scheduler, backfill, DB ops
│           └── TickerPage.tsx     # Chart, indicators, fundamentals, data quality
├── pyproject.toml
├── uv.lock
├── alembic.ini
├── .env.example
├── .gitignore
├── .dockerignore
└── .python-version                # 3.12
```

---

## Data Flow Diagram

```
┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐  ┌──────────────┐
│ Alpaca Market  │  │ arkfunds.io    │  │ SEC EDGAR API  │  │ Senate eFD   │  │ yfinance     │
│ Data API ⭐    │  │ (ARK Holdings) │  │ (Form 4/13F)   │  │ (Politician) │  │ (Fund/Rtg/Ea)│
└────────┬───────┘  └────────┬───────┘  └────────┬───────┘  └──────┬───────┘  └──────┬───────┘
         │                   │                   │                 │                 │
         ▼                   ▼                   ▼                 ▼                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                Collectors (Python)                                             │
│  [prices_alpaca] [ark_holdings] [form4] [form13f] [politicians] [fund] [ratings] [earnings]   │
└────────────────────────────────┬───────────────────────────────────────────────────────────────┘
                                 │ INSERT / UPSERT
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│              Raw Layer (append-only / upsert)                                        │
│  prices_daily | ark_holdings | insider_trades | politician_trades                    │
│  fundamentals_snapshot | analyst_ratings | earnings_calendar                          │
└────────────────────────────────┬─────────────────────────────────────────────────────┘
                                 │ SELECT + COMPUTE
                                 ▼
┌─────────────────────────────────────────────────────────┐
│              Derived Layer (recomputable)               │
│  ark_deltas | insider_clusters | technical_indicators   │
└────────────────────────┬────────────────────────────────┘
                         │ AGGREGATE
                         ▼
┌─────────────────────────────────────────────────────────┐
│       Feature Store (feature_snapshots) ⭐              │
│     One feature vector per ticker per day               │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
         ┌───────────────┴────────────────┐
         ▼                                ▼
┌──────────────────┐          ┌──────────────────────┐
│ Jupyter Analysis │          │ Scoring (later)      │
│ ML Experiments   │          │ Trading Signals      │
└──────────────────┘          └──────────────────────┘
```

---

## Key Technical Decisions

For detailed rationale behind all decisions, see:
- [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) – Infrastructure & deployment
- [DECISIONS_DATA.md](DECISIONS_DATA.md) – Data sources & collectors
- [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md) – Features & trading strategy
