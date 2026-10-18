# ARCHITECTURE.md – Technical Architecture

> System architecture, deployment topology, project structure, and data flow.
> For database schema details, see [DATABASE.md](DATABASE.md).
> For execution schedule (all job times), see [SCHEDULER.md](SCHEDULER.md).
> For data sources, APIs and rate limits, see [DATA_SOURCES.md](DATA_SOURCES.md).
>
> See also: [INDEX.md](INDEX.md)

**Last updated:** October 2026

---

## Deployment Topology

```
┌─────────────────────────────────────────────────────────────┐
│  Windows Machine (Development)                              │
│  ├─ Claude Code (Claude desktop app)                        │
│  └─ Git Client → GitHub (sebastianganser/Alpaca-Broker)     │
└────────────────────────┬────────────────────────────────────┘
                         │ git push  →  git pull on Unraid
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Unraid Server (Production) – 192.168.1.93                           │
│                                                                      │
│  ┌──────────────────────────────┐     ┌────────────────────────────┐ │
│  │ alpaca-broker (ONE container)│     │ postgresql18-alpaca        │ │
│  │ user 99:100 (nobody:users)   │────▶│ (separate container,       │ │
│  │                              │     │  not managed by this repo) │ │
│  │ uvicorn / FastAPI  :8090     │     │ PostgreSQL 18, port 5435   │ │
│  │ + APScheduler                │     │ DB: broker_data            │ │
│  │   (BackgroundScheduler)      │     │ Schema: signals            │ │
│  │ + React SPA (frontend/dist)  │     └────────────────────────────┘ │
│  │ + FinBERT model (in image)   │                                    │
│  └──────────────┬───────────────┘                                    │
│                 │ bind mount                                         │
│                 ▼                                                    │
│  /mnt/user/Workfiles/AlpacaBroker/context_packs/  (Context Packs,    │
│                                    forecasts.yaml, forecast report)  │
└──────────────────────────────────────────────────────────────────────┘
          │ HTTPS (outbound only)
          ▼
  Alpaca (Paper Trading ONLY – Market Data + News), SEC EDGAR, arkfunds.io,
  Senate eFD, yfinance, FRED, Massive (ex-Polygon), OpenFIGI, Wikipedia
  → details: DATA_SOURCES.md
```

### Container

| Aspect | Implementation |
|--------|----------------|
| Compose file | [infra/docker-compose.yml](../infra/docker-compose.yml) – only the service `alpaca-broker` (image `alpaca-broker:latest`); the DB runs separately |
| Port | `8090:8090` (API + UI); health endpoint `GET /api/v1/health` |
| Env | `env_file: /mnt/user/appdata/alpaca-broker/.env`, `TZ=Europe/Berlin` (all job times are Berlin time) |
| Volume | `/mnt/user/Workfiles/AlpacaBroker` → same path in the container; `CONTEXT_PACK_PATH` defaults to `/mnt/user/Workfiles/AlpacaBroker/context_packs` |
| Shutdown | `stop_grace_period: 40s`; the FastAPI lifespan waits up to 25 s for running jobs (`SHUTDOWN_TIMEOUT_SECONDS`), runs still `running` afterwards are marked stale on next start |
| Logging | Docker `json-file`, 3 × 10 MB |

**[infra/Dockerfile.collector](../infra/Dockerfile.collector)** – multi-stage build:

1. `node:20-alpine` – `npm ci` + `npm run build` of the React SPA → `frontend/dist`
2. `python:3.12-slim` + pinned `uv` (0.11.2) – `uv sync --frozen`, copies `src/`, `scripts/`, `alembic.ini`; pre-downloads the FinBERT model (`ProsusAI/finbert`) into `/app/.cache/huggingface`
3. `python:3.12-slim` runtime – `postgresql-client` (for `pg_isready`), non-root user **`99:100`** (`APP_UID`/`APP_GID` build args, Unraid `nobody:users`), `HEALTHCHECK` every 60 s against `http://localhost:8090/api/v1/health` (start period 120 s; the endpoint returns 503 if the scheduler is not running or the DB is unreachable)

`CMD` = `.venv/bin/python -m trading_signals.main` (uvicorn on `0.0.0.0:8090`).

**[infra/entrypoint.sh](../infra/entrypoint.sh)**: waits for the DB (`pg_isready` on `DB_HOST:DB_PORT`) → `alembic upgrade head` (**fails hard** – the container exits and `restart: unless-stopped` retries) → `exec` CMD.

**Startup sequence** ([main.py](../src/trading_signals/main.py) lifespan): `validate_alpaca_safety()` (refuses to start if `ALPACA_ENDPOINT` does not contain `paper`) → `create_scheduler()` → job tracker + alert listeners → `scheduler.start()` → `mark_stale_runs()` → `schedule_startup_catchup()`.

### Deploy Workflow

```bash
# Manual route (Sebastian, Unraid web terminal)
cd /mnt/user/appdata/alpaca-broker && git pull
# then either: Compose Manager → Stack "Alpaca-Broker" → Compose Down → Compose Up (rebuild)
# or directly:
docker compose -f /boot/config/plugins/compose.manager/projects/Alpaca-Broker/docker-compose.yml up --build -d
```

Migrations run automatically on container start. Claude Code deploys through the Unraid User Script `alpaca-ops` with the argument `deploy` (see [Operations Access](#operations-access-claude-code); skill `.claude/skills/deploy`): git pull → `docker compose build` → `up -d`, detached, so the old container keeps serving during the ~7 min build and is only recreated once the image is built. [scripts/repair/deploy_review_fixes.sh](../scripts/repair/deploy_review_fixes.sh) is a one-off helper from the 2026-10-05 review (git pull → rebuild → wait for `healthy` → repair scripts in the container); it is not the regular deploy path.

**Network:** the container connects to PostgreSQL via host IP (`DB_HOST` default `192.168.1.93`, `DB_PORT` default `5435`).
**Backup:** by decision (2026-10-05, CLAUDE.md principle 2) **no database backups** are kept; the warehouse is a rolling 20-quarter window. Older docs mentioned a daily `pg_dump` cron on Unraid – checked 2026-10-16 (`alpaca-ops status`): no cron entry and no User Script references the alpaca DB.
**In production since:** 13 April 2026.

### Operations Access (Claude Code)

Claude Code reaches the production system through three channels (no SSH):

| Channel | Access | Used for |
|---------|--------|----------|
| Production API | `http://192.168.1.93:8090/api/v1/…`, read-only `GET` via `curl`: `health`, `dashboard/summary` (collectors with `last_status`/`is_running`, `table_stats`, `system_health` incl. Alembic revision and job count), `logs` (filters `status`, `collector`, `page`, `limit`) | Health and log checks (agent `log-checker`, skill `log-check`) |
| Context-pack share | `\\HOMENAS\Workfiles\AlpacaBroker\context_packs\` – read-only, by hostname (access via IP is denied) | Context packs, `forecasts.yaml`, `forecast_auswertung.md` |
| Unraid WebGUI | `http://192.168.1.93` in Claude Code's built-in browser pane; Sebastian logs in himself. One User Script **`alpaca-ops`** (Settings → User Scripts → Run Script, argument = command: `status`, `logs [N] [SINCE]`, `deploy`, `deploy-log`, `restart`, `run scripts/…py [args]`, and for long runs `run-bg scripts/…py [args]` / `run-log [N]` / `run-stop` – detached, output in `ops/bg-job.log`). The wrapper fetches [infra/unraid/alpaca-ops.sh](../infra/unraid/alpaca-ops.sh) from `origin/main` on every run (a push changes the ops logic, the deployed app only changes with `deploy`) and mirrors each run's output to `\\HOMENAS\Workfiles\AlpacaBroker\ops\<command>.log` | Deploy (skill `deploy`), container state, container logs, project scripts in the container |

Scope and guard rules: in the WebGUI only the container `alpaca-broker`, its folders and its own User Scripts may be touched – never other containers (GynOrg, PostgreSQL), array, shares, users, network or security settings. Deploys/restarts are not allowed between 22:15 and 05:15 (collectors + nightly chain), while a job is running (`is_running` in `dashboard/summary`) or while a project script runs in the container (`docker top`; started via `run` or `run-bg`). `run` and `run-bg` are refused in the night window at the start; a detached `run-bg` is not stopped at 22:15, so it must end before the chain (04:30, Tue–Sat) or be stopped with `run-stop` (decision [2026-10-17](DECISIONS_ARCHITECTURE.md)). The shared Claude Code setup (permissions, subagents, skills) lives in `.claude/`; the working rules are in [CLAUDE.md](../CLAUDE.md).

---

## Project Structure

```
Alpaca-Broker/
├── CLAUDE.md / README.md            # Project entry point / GitHub overview
├── .claude/                         # Claude Code project setup (shared via Git)
│   ├── settings.json                # Shared permissions (settings.local.json is git-ignored)
│   ├── agents/                      # Subagents: log-checker (Haiku), doc-keeper (Sonnet), code-reviewer (Opus)
│   └── skills/                      # Workflows: log-check, deploy, session-end
├── docs/                            # Documentation (INDEX.md = navigator)
│   ├── FORECASTS.md                 # forecasts.yaml format + pre-registered evaluation (STAGE2_DECISIONS.md = historical)
│   ├── 2026-10-06_Konzept_Kurzfrist_Kandidaten.md  # short-term candidate concept
│   └── reports/                     # Research reports (event studies, walk-forward, S&P 500 survivorship
│                                    #   inventory incl. repair runbook, …)
├── infra/
│   ├── docker-compose.yml           # Service alpaca-broker only (DB runs separately)
│   ├── Dockerfile.collector         # 3-stage build (Node → uv/Python → slim runtime, user 99:100)
│   ├── entrypoint.sh                # pg_isready → alembic upgrade head → CMD
│   └── unraid/
│       └── alpaca-ops.sh            # Host ops (status/logs/deploy/run/run-bg), run via User Script "alpaca-ops"
├── src/
│   ├── alembic/versions/            # Migrations 001–038
│   └── trading_signals/             # Python package (see below)
├── scripts/                         # Run manually (locally or via docker exec)
│   ├── repair/                      # One-off repairs/backfills (features_rebuild, refetch prices,
│   │                                #   backfill_short_term_features, form13f_resolve_tickers,
│   │                                #   collect_short_interest, backfill_short_interest_features,
│   │                                #   rebuild_sp500_membership + onboard_removed_members – S&P 500
│   │                                #   survivorship repair, dry run by default, …)
│   ├── analysis/                    # Research: screen_backtest (neutral screen validation),
│   │                                #   simulate_criterion_size (size/power of the forecast criterion),
│   │                                #   event_study (horizon + barrier mode), collect_buyback_events,
│   │                                #   backtest_barrier, walkforward_hit_model (barrier, removed in plan step 6)
│   ├── archive/                     # Obsolete one-time scripts (kept for reference)
│   ├── sprint10_readiness.py        # Data-maturity check for the scoring model
│   └── *.py                         # Universe init/sync/validate, recompute helpers, backfills
├── notebooks/                       # Exploratory analyses (01–03, plain .py)
├── frontend/                        # Vite + React SPA (built into the image)
│   └── src/  App.tsx, Layout.tsx, api.ts, queries.ts, components/, hooks/,
│             pages/ Dashboard, Universe, Signals (ARK/Insider/Politician/Ratings/Sentiment tabs),
│                    Features, Logs, Settings (scheduler, backfill, API key), Ticker
├── tests/
│   ├── unit/                        # ~50 test modules (pytest)
│   └── fixtures/
├── pyproject.toml / uv.lock / .python-version (3.12)
├── alembic.ini
└── .env.example                     # Template – real values only in .env (never committed)
```

### `src/trading_signals/`

```
trading_signals/
├── main.py                 # FastAPI app + lifespan (scheduler start/stop, catch-up), /api/v1/health, SPA fallback
├── config.py               # Pydantic Settings (DB, Alpaca paper endpoint + safety check, API keys,
│                           #   CONTEXT_PACK_PATH, API_KEY, ALERT_WEBHOOK_URL, LOG_LEVEL)
├── api/
│   ├── deps.py             # DB session, scheduler DI, require_write_access (X-API-Key / X-Requested-With)
│   ├── job_tracker.py      # APScheduler listener → currently running job ids for the UI
│   ├── schemas.py          # Pydantic response schemas
│   ├── serializers.py      # Shared ORM → schema serializers (/signals and /ticker)
│   ├── tasks.py            # BackfillManager (background threads with progress)
│   └── routes/             # dashboard, universe, signals, ticker, features, logs,
│                           #   operations (/ops/*), analysis (/analysis/*)
├── collectors/             # One module per source; BaseCollector subclasses log + alert themselves
│   ├── base.py             # Template method: gaps → fetch → store → collection_log
│   ├── _db.py / _parsing.py# Shared bulk-upsert / parsing helpers
│   ├── gap_detector.py     # Missing trading days: refetch, else forward-fill (is_extrapolated)
│   ├── prices_alpaca.py    # Daily OHLCV (Alpaca, SIP feed, IEX fallback); triggers recompute on re-adjustment
│   ├── ark_holdings.py     # ARK ETF holdings (arkfunds.io) + universe expansion
│   ├── sec_client.py       # SEC EDGAR client (rate limit, CIK mapping)
│   ├── form4_collector.py  # Insider trades (Form 4)
│   ├── form13f_collector.py# Institutional holdings (13F-HR, top filers)
│   ├── sec_filings_collector.py # SEC filings (8-K + items, 10-Q/K, 144, 13D/G, S-1/S-3, DEF 14A) + press release / 8-K excerpts (EDGAR submissions API, daily 04:00)
│   ├── sec_financials_collector.py / sec_xbrl.py # XBRL quarterly financials (companyfacts API, weekly Sunday) / pure companyfacts parser
│   ├── cusip_resolver.py   # CUSIP → ticker (cusip_map cache, local tables, OpenFIGI)
│   ├── disclosure_client.py / politician_trades_collector.py  # Senate eFD PTRs
│   ├── yfinance_client.py  # Shared yfinance client (rate limit, batching, backoff)
│   ├── fundamentals_collector.py / analyst_ratings_collector.py /
│   │   earnings_calendar_collector.py / estimates_collector.py   # yfinance sources
│   ├── options_iv_collector.py   # ATM IV, skew, term structure, put/call OI (yfinance)
│   ├── short_interest_collector.py # Short volume (Massive API, ex-Polygon)
│   ├── short_interest_positions_collector.py # FINRA short interest, twice-monthly settlements (Massive API, weekly)
│   ├── fred_collector.py   # 6 FRED macro series
│   ├── news_collector.py   # Alpaca News API (Benzinga)
│   └── buyback_events.py   # 8-K buyback authorizations (EDGAR full-text) – research only,
│                           #   used by scripts/analysis, not scheduled
├── derived/
│   ├── technical_indicators.py   # SMA/EMA/RSI/MACD/Bollinger/ATR/RS vs SPY (catch-up logic)
│   ├── ark_deltas.py             # ARK position changes (after ark_holdings)
│   ├── insider_clusters.py       # Cluster buys (after form4)
│   ├── sentiment_scorer.py       # FinBERTScorer (CPU) behind a SentimentScorer ABC
│   ├── sentiment_computer.py     # Scores unscored articles → news_sentiment
│   ├── short_term_features.py    # 9 st_* price features (1–14 session horizon)
│   ├── short_interest_features.py # 2 FINRA short-interest features (9-session lag; pure + vectorised, kept identical by a test)
│   ├── feature_pipeline.py       # ~97 features / 17 groups (incl. st_*, short_interest_*) per PIT-universe ticker and session
│   ├── target_backfill.py        # Forward returns 1/5/20/60d + barrier label (return_barrier_14d, …)
│   ├── recompute.py              # Recompute TA, targets, st_* after a price-history refresh
│   ├── context_pack/             # Context pack v2 ([CONTEXT_PACK.md](CONTEXT_PACK.md)): generator.py (entry point `ContextPackGenerator`), loader.py (batch SQL, the only DB access),
│   │                             #   pit.py (point-in-time rules), model.py (gate-universe percentiles), sections/ (13 ticker-file sections), ticker_file.py, overview.py,
│   │                             #   screen_csv.py, template.py (forecasts.yaml template), fmt.py, rows.py, constants.py
│   ├── forecasts.py              # Parses/validates forecasts.yaml (forecasts/v1) → forecast_reviews / forecasts (append-only, idempotent by file hash; on time only with a seal)
│   ├── forecast_seal.py          # Job forecast_seal: SHA-256 of forecasts.yaml before the open → forecast_file_seals (the on-time proof)
│   └── pack_candidates.py        # Screen candidates of a pack → pack_candidates (called by the pack generator, plan step 3)
├── analysis/
│   ├── feature_report.py   # Monthly FeatureAnalysisEngine (daily rank IC, excess returns) → analysis_reports
│   ├── stats.py / modeling.py / feature_groups.py  # Pure stats (Newey-West, rank IC, batch-jackknife intervals), leakage-safe RF+LASSO, feature grouping
│   ├── screen.py           # Neutral candidate screen: quality gate, 12 evidence groups with literature signs, buckets (SCREEN.md)
│   ├── screen_backtest.py  # Historical validation of the screen (absolute / relative / SPY; scripts/analysis/screen_backtest.py)
│   ├── forecast_eval.py    # Pre-registered evaluation of Claude's forecasts; freeze_sample / verdict_from_frozen = decisive criterion (FORECASTS.md)
│   ├── criterion_sim.py    # Simulated size / power of that criterion (scripts/analysis/simulate_criterion_size.py)
│   ├── benchmark.py        # SPY forward returns on the target definition (read-only loader)
│   ├── report_format.py    # Markdown cell formatters shared by the analysis reports
│   ├── event_study.py      # Event studies: horizon mode (20/60-session raw and excess returns) + barrier mode (barrier mode removed in step 6)
│   ├── barrier.py          # Barrier trade simulation (MAIN_BARRIER: +1 % / −2 % / 14 sessions) – removed in plan step 6
│   ├── hit_model.py        # Walk-forward hit-probability model for the barrier label – removed in step 6
│   └── forecast_review.py  # Nightly step glue: loaders, freeze / verdict persisted ONCE (forecast_criterion_samples), report forecast_auswertung.md
├── scheduler/
│   ├── setup.py            # JOB_DEFINITIONS (cron, Europe/Berlin) + create_scheduler()
│   ├── registry.py         # job id ↔ collector_name, CHAIN_STEP_JOB_IDS, NIGHTLY_UPSTREAM, CATCHUP_JOBS
│   ├── runner.py           # run_logged_job, mark_stale_runs, startup catch-up, alert listeners
│   ├── jobs.py             # Job functions incl. run_nightly_chain, retention, feature_analysis
│   └── sector_enrichment.py# Sector/industry via yfinance (index_sync + UI backfill)
├── universe/
│   ├── manager.py          # Universe CRUD + get_universe_as_of (point-in-time universe)
│   ├── index_sync.py       # Monthly S&P 500 / Nasdaq 100 sync: effective change dates, rename re-keying
│   ├── wikipedia_index_history.py  # Change-table parser ("Historical components of …"), ParseResult.complete,
│   │                       #   interval generation, GitHub CSV cross-check; incomplete source → no seeding
│   ├── ticker_aliases.py   # 24 documented renames (current-symbol convention), AliasMap, symbol re-use list
│   ├── corporate_actions.py# Alpaca corporate actions (name_change, mergers), read-only
│   ├── validation.py       # Weekly universe validation against Alpaca assets (job universe_validation)
│   ├── alpaca_validator.py / onboarder.py / blacklist.py  # Tradeability, auto-onboarding, ETF filter (never blacklists a ticker with an open index_membership interval)
├── data/congress_members.py# Static Congress member → party mapping
├── db/
│   ├── base.py             # Declarative base (schema signals), now_local()
│   ├── session.py          # Engine + session factory
│   └── models/             # ORM models (one module per domain, see DATABASE.md)
└── utils/
    ├── market_calendar.py  # NYSE calendar: trading_days, last_completed_session, …
    ├── job_status.py       # Canonical collection_log statuses
    ├── alerting.py         # notify_run_status → ALERT_WEBHOOK_URL (ntfy)
    ├── retention.py        # DATA_RETENTION_QUARTERS = 20, data_start_date(), quarter_cutoff()
    ├── repair_support.py   # Helpers of the repair scripts: DB target banner, read-only dry-run session, verified CSV backup
    ├── logging.py          # Logging setup, secret redaction, CollectorLogCapture
    └── retry.py            # Retry with exponential backoff (429/5xx, Retry-After)
```

---

## Data Flow

```mermaid
flowchart TD
    SRC["External sources: Alpaca prices + news, arkfunds.io, SEC EDGAR, Senate eFD, yfinance, FRED, Massive"] --> COL["Collectors (scheduled, BaseCollector)"]
    COL --> RAW["Raw tables: prices_daily, ark_holdings, insider_trades, form13f_holdings, politician_trades, fundamentals_snapshot, analyst_ratings, earnings_calendar, estimates_snapshot, options_iv_snapshot, short_volume, short_interest, macro_series, news_articles"]
    RAW --> DER["Derived: technical_indicators, ark_deltas, insider_clusters, news_sentiment (FinBERT)"]
    RAW --> CHAIN
    DER --> CHAIN["Nightly chain 04:30"]
    CHAIN --> FS["feature_snapshots (features incl. st_*)"]
    FS --> TGT["Targets: return_1d/5d/20d/60d + barrier label"]
    TGT --> S2["forecast_review: forecasts.yaml → forecast_reviews / forecasts, frozen sample + verdict (once), forecast_auswertung.md"]
    FS --> CP["Context Pack v2 (quality gate + neutral screen, screen.csv, ticker files, pack_candidates) → Unraid share"]
    CP -. "Claude broker skill writes forecasts.yaml" .-> S2
    TGT --> FA["feature_analysis (monthly, 1st 07:00) → analysis_reports"]
```

Universe maintenance runs alongside: `index_sync` (monthly) maintains `universe` + `index_membership`; `universe_validation` (weekly, Sunday 13:00) deactivates delisted/renamed tickers; ARK/politician collectors onboard new tickers via `universe/onboarder.py`; 13F CUSIPs are resolved via `cusip_map`.

**Point-in-time universe:** the feature pipeline computes snapshots for `UniverseManager.get_universe_as_of(d)` = tickers with an `index_membership` interval containing `d` (`valid_from <= d < valid_to`), **∪** universe tickers held by an ARK ETF in the 30 days before `d` (`ARK_UNIVERSE_WINDOW_DAYS`). Without any membership data it falls back to `is_active = TRUE`. `index_membership` follows one convention: a ticker rename is a single interval under the **current** symbol (old symbols get none), a seed or rebuild needs a source that lists removals as well as additions ([DECISIONS_DATA.md](DECISIONS_DATA.md) 2026-10-16, [DATABASE.md](DATABASE.md)). The point-in-time rules per source are documented in the module docstring of [feature_pipeline.py](../src/trading_signals/derived/feature_pipeline.py).

### Nightly Chain

Defined in [scheduler/jobs.py](../src/trading_signals/scheduler/jobs.py) (`run_nightly_chain`) and [scheduler/registry.py](../src/trading_signals/scheduler/registry.py). Scheduled daily at **04:30 Europe/Berlin**, after all upstream collectors of the night (times: [SCHEDULER.md](SCHEDULER.md)).

```mermaid
flowchart TD
    A["04:30 nightly_chain"] --> B["target = last completed NYSE session"]
    B --> C{"SUCCESS run with notes session=target exists?"}
    C -- yes --> Z["SKIPPED (no new session)"]
    C -- no --> D["wait_for_upstream: poll every 5 min, max 2 h, while stale upstream collectors are still running"]
    D --> E["1. technical_indicators catch-up"]
    E --> F["2. feature_pipeline(target)"]
    F --> G["3. target_backfill (returns + barrier label)"]
    G --> H["4. forecast_review"]
    H --> I{"feature_pipeline SUCCESS or PARTIAL?"}
    I -- yes --> J["5. context_pack_generator(target): pack v2"]
    I -- no --> K["context pack SKIPPED"]
    J --> L["overall status → collection_log nightly_chain"]
    K --> L
```

- **Target session** = `market_calendar.last_completed_session()` (not `today − 1`; the container runs in Berlin time).
- **Upstream check** (`NIGHTLY_UPSTREAM`): `prices_alpaca`, `options_iv_collector`, `short_interest_collector`, `fred_collector`, `estimates_collector`, `analyst_ratings_collector`, `ark_holdings`, `form4_collector`, `news_alpaca`, `sentiment_computer`. A collector is fresh if it has a `success`/`partial` run that started after 16:00 ET of the target session. The chain only waits while stale collectors are still `running`; afterwards it proceeds and records them as `stale inputs`. (`prices_alpaca` is flagged critical in the registry, but the chain currently treats every stale input the same → `partial`.)
- **Steps** each run through `run_logged_job(..., alert=False)` and get their own `collection_log` row. `feature_pipeline` writing 0 rows → `skipped`; `target_backfill` retries up to 3× on PostgreSQL deadlocks; `forecast_review` runs after the target update so newly realised returns are scored (two transactions: ingest first, then freeze / verdict and the report).
- **Overall status:** `failed` if the feature pipeline failed; `partial` if inputs were stale or any step was not `success`; else `success`. Notes contain `session=YYYY-MM-DD; <step>=<status>, …`. Only the chain itself alerts.
- **Chain step jobs** (`feature_pipeline`, `target_backfill`, `forecast_review`, `context_pack_generator`, see `CHAIN_STEP_JOB_IDS`) are registered **paused** (`next_run_time=None`): visible and manually triggerable in the UI, but never fire on their own. A per-`collector_name` lock in `run_logged_job` prevents a manual trigger and the chain from running the same step concurrently (→ `skipped`).

---

## Context Pack Output (Unraid share)

Written by [derived/context_pack/](../src/trading_signals/derived/context_pack/) (nightly chain step `context_pack_generator`; format reference for the skill author: [CONTEXT_PACK.md](CONTEXT_PACK.md)) and the `forecast_review` step below `CONTEXT_PACK_PATH` (default `/mnt/user/Workfiles/AlpacaBroker/context_packs`):

```
context_packs/
├── requested_tickers.txt         # optional: tickers that always get a full file (one per line)
├── forecast_auswertung.md        # Forecast evaluation report (German), rewritten on every forecast_review run
└── 2026-10-16/                   # one folder per market session (target session, ISO date)
    ├── 00_uebersicht.md          # overview: regime, coverage, gate counts, alphabetical file list, forecasts.yaml template + deadline
    ├── screen.csv                # every ticker that passed the quality gate: group percentiles, breadth, composite, flags (no bucket)
    ├── AAPL.md  AMD.md  …        # full files of the screened tickers, alphabetical, ~9 KB each
    └── forecasts.yaml            # written by the Claude broker skill (forecasts/v1, FORECASTS.md), read by forecast_review
```

- **Selection (pack format 2):** the quality gate and the neutral screen `screen/v1` ([SCREEN.md](SCREEN.md)) on the session's `feature_snapshots`: ≤ 10 bullish candidates, 10 bearish (switched off by configuration), 5 random controls, plus the tickers of `requested_tickers.txt`. The files are alphabetical and carry no hint why they are in the pack; the buckets are stored in `pack_candidates` (same transaction, replaced on a re-run).
- **Ticker files:** YAML frontmatter (`pack_format: 2`, `ticker`, `session`, `sector`, `data_freshness`) plus 13 sections (head, trend, valuation, fundamentals, estimates / analysts, earnings, smart money, positioning, news, sector, liquidity, data quality, regime). Every value is the raw value plus its percentile among the gate tickers; NULL is shown as "keine Abdeckung", never as 0. Raw-table sections (Form 4 trades, rating actions, headlines, 13F holders, politicians, short-interest settlements) are read with exactly the point-in-time cut-offs of the feature pipeline.
- **Encoding:** all files (packs, `screen.csv` and `forecast_auswertung.md`) are written as `utf-8-sig` (UTF-8 with BOM).
- **Re-runs:** old `TICKER.md` / legacy `NN_*.md` files of the same day are deleted before writing; `forecasts.yaml` and unknown files are never touched. The overview is written last.
- **Forecasts:** `forecast_review` scans all session folders for `forecasts.yaml` (schema `forecasts/v1`, format and pre-registered evaluation: [FORECASTS.md](FORECASTS.md)), stores each new file once (SHA-256, only after the deadline of its pack day, each file in its own savepoint) in `forecast_reviews` / `forecasts` (append-only; a changed file is a new review; `on_time` only if `decided_at` and the file's seal from the weekday job `forecast_seal` precede the 09:30 New York open), freezes the decisive sample of a forecaster version when it is complete and computes its verdict once (`forecast_criterion_samples`), and writes `forecast_auswertung.md` (blinded: counts only until a version's verdict exists). A rejected file or row → run status `partial` (alert).

> [!IMPORTANT]
> **File permissions:** the container runs as `99:100`. The host directory must be owned by `99:100`:
> `chown -R 99:100 /mnt/user/Workfiles/AlpacaBroker`.
> Running scripts via `docker exec -u root …` creates root-owned files/folders that the app later cannot overwrite (e.g. a session folder or `forecast_auswertung.md`) – run scripts as the default container user, or re-`chown` afterwards. (The compose file contains a commented-out `user: "0:0"` fallback; fixing ownership is the preferred solution.)

---

## Cross-Cutting Concerns

### Job execution & `collection_log`

- **Collector jobs** (`BaseCollector` subclasses) write their own `collection_log` row and alert themselves (wrapped by `_run_collector` in `jobs.py`; missing API keys → `skipped` for FRED / options IV).
- **All other jobs** (derived computations, chain, retention, feature analysis) run through `run_logged_job` ([scheduler/runner.py](../src/trading_signals/scheduler/runner.py)): inserts a `running` row, captures log lines into `collection_log.log_lines` (secrets redacted), catches exceptions → `failed`, finalizes status/records/notes, sends alerts.
- **Statuses** ([utils/job_status.py](../src/trading_signals/utils/job_status.py)): `running`, `success`, `partial`, `failed`, `skipped`; legacy values (`error`, `complete`, `completed`) are normalized. `registry.JOB_COLLECTOR_NAMES` maps scheduler job ids to `collector_name` (e.g. `price_collector` → `prices_alpaca`, `news_collector` → `news_alpaca`, `fundamentals_collector` → `fundamentals_yf`).
- **Stale runs:** rows still `running` after 6 h are set to `failed` (`stale: process restart`) at startup and by the daily `log_retention` job.

### Alerting

[utils/alerting.py](../src/trading_signals/utils/alerting.py): `notify_run_status` POSTs a plain-text message to `ALERT_WEBHOOK_URL` (ntfy-compatible) for `partial`/`failed` runs; without a URL alerts are only logged, and alerting never raises. Additionally APScheduler listeners alert on uncaught job exceptions (`EVENT_JOB_ERROR`) and missed runs (`EVENT_JOB_MISSED`).

### Startup catch-up

The job store is in-memory, so APScheduler cannot detect misfires across restarts. `schedule_startup_catchup` re-runs the jobs in `CATCHUP_JOBS` once if their last scheduled fire time passed without a `success`/`partial`/`skipped` run and the next regular run is more than 1 h away: `price_collector` (+2 min), `estimates_collector` (+4 min, rolling 90-day revision window), `nightly_chain` (+15 min; waits for upstream itself).

### Retention

- **Data retention** (weekly, Sunday 03:00): keeps the last **20 calendar quarters** (`DATA_RETENTION_QUARTERS`, cutoff `quarter_cutoff(20)`). Deletes per table in its own savepoint; excludes `earnings_calendar`, `collection_log`, `universe`, `ticker_blacklist`, `index_membership` and the forecast evaluation records (`forecast_reviews`, `forecasts`, `forecast_criterion_samples`, `forecast_file_seals`, `pack_candidates`). Backfills must use `retention.data_start_date()` and never collect data before the window.
- **Log retention** (daily 03:30): deletes `collection_log` rows older than **90 days** and marks stale runs.

### API security

GET/HEAD/OPTIONS requests are always allowed. Mutating requests on the `operations` (`/api/v1/ops/*`) and `analysis` routers (e.g. `POST /analysis/trigger`) require `X-API-Key` matching `API_KEY` if configured, otherwise the header `X-Requested-With` (CSRF guard) – see `require_write_access` in [api/deps.py](../src/trading_signals/api/deps.py). The API key can be entered in the UI (Settings → API key card).

### Trading safety

Paper trading only: `Settings.validate_alpaca_safety()` aborts startup if `ALPACA_ENDPOINT` does not contain `paper`. The system collects data and produces research output; it does not place orders.

---

## Key Technical Decisions

For detailed rationale behind all decisions, see:
- [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) – Infrastructure & deployment
- [DECISIONS_DATA.md](DECISIONS_DATA.md) – Data sources & collectors
- [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md) – Features & trading strategy
- [2026-10-06_Konzept_Kurzfrist_Kandidaten.md](2026-10-06_Konzept_Kurzfrist_Kandidaten.md) – Short-term candidate concept (barrier trade, risk pre-filter, stage 2)
