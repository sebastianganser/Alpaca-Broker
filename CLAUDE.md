# CLAUDE.md – Alpaca-Broker Project

> **Entry point for every Claude Code session on this project** (loaded automatically).
> Keep this file short: details live in `docs/` – full catalog in [docs/INDEX.md](docs/INDEX.md).

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
Raw data is never modified by evaluations. Evaluations, scores, and signals are **computed** from raw data and can be recomputed any time if the algorithm changes.

**Rolling retention (decision 2026-10-05):** The system keeps exactly the last **20 calendar quarters** (~5 years). Older data is deleted weekly by the `data_retention` job and must not be re-collected by backfills. **No database backups** are kept. Use `trading_signals.utils.retention.data_start_date()` instead of hard-coded start dates.

### 3. Deterministic Core, LLM Only at the Edges
Critical paths (data fetching, computations, later order execution) are pure Python code with unit tests. LLMs are only used for unstructured tasks (news parsing, report generation, ad-hoc analyses).

---

## Current Status

**Phase:** 🟡 Sprint 9.5c (Candidate Pipeline MVP), Phase 2 – review fixes deployed, short-term research K1–K4 finished without a usable edge, K5 (stage-2 forward test) running
**Current sprint:** 9.5c Phase 2 – collect stage-2 decisions (K5), keep data quality stable; F1 composite score open
**Next step:** decide how to repair the S&P 500 removal history in `index_membership` (survivorship gap found 16.10. – see ROADMAP "Offene Punkte"); schedule universe validation weekly. Keep collecting data; evaluate FINRA short interest in the monthly analysis (01.11.) and the Sprint 10 readiness re-run in early December. Forward testing (K5) waits until after the sprints – see [SESSION_LOG.md](docs/SESSION_LOG.md), Session 33
**Last updated:** 16 October 2026 (FINRA short-interest source implemented, deploy pending)
**Deployment:** ✅ Unraid Docker (192.168.1.93:8090), commit `b873d4d` (deployed 16 October 2026 18:14 via `alpaca-ops deploy`), Alembic revision `036`

See [ROADMAP.md](docs/ROADMAP.md) for detailed progress.

---

## Quick Facts

| Property | Value |
|---|---|
| **Language** | Python 3.12+ |
| **Backend API** | FastAPI (same process as APScheduler) |
| **Frontend** | Vite + React SPA (Stitch "Precision Architect" Design) |
| **ORM** | SQLAlchemy 2.0 |
| **Migrations** | Alembic (001–036) |
| **Database** | PostgreSQL 18 (`postgresql18-alpaca`, 192.168.1.93:5435, DB: `broker_data`, schema: `signals`) |
| **Package managers** | uv (Python), npm (frontend) |
| **Scheduler** | APScheduler, 25 jobs (`scheduler/setup.py`): 13 daily (collectors, `log_retention`, `nightly_chain` 04:30), 6 weekly (incl. `data_retention`, FINRA `short_interest_positions_collector`), 2 monthly (`index_sync`, `feature_analysis`), 4 paused chain steps run by `nightly_chain` |
| **Price data** | Alpaca Market Data API, **SIP feed** (`ALPACA_DATA_FEED`, default `sip`; production logs show `feed=sip`), automatic IEX fallback marks the run `partial` |
| **News sentiment** | Alpaca News API + ProsusAI/finbert (local CPU inference) |
| **Universe** | 757 active tickers (2026-10-16, after deactivating 10 delisted/renamed; S&P 500 + Nasdaq 100 + ARK + benchmark/sector ETFs, other ETFs filtered via blacklist); features use the point-in-time universe |
| **Tests** | 1022 tests, none needs a database (2026-10-16); run with `uv run pytest` |
| **Deployment** | Docker Compose on Unraid (1 container: Collector + API + UI, port 8090, runs as `99:100`) |
| **Broker (later)** | Alpaca Paper Trading (NEVER live!) |
| **Version control** | Git, [GitHub](https://github.com/sebastianganser/Alpaca-Broker) |

## Commands

Dev machine: Windows 11, working directory = project root. The Bash tool is Git Bash (share paths as `//HOMENAS/...`); PowerShell is also available.

| Task | Command |
|---|---|
| Unit tests (none needs a DB) | `uv run pytest` · single test: `uv run pytest tests/unit/test_barrier.py -k <name>` |
| Prove DB isolation | `DB_HOST=127.0.0.1 DB_PORT=1 uv run pytest` (PowerShell: `$env:DB_HOST='127.0.0.1'; $env:DB_PORT='1'; uv run pytest`) |
| Lint | `uv run ruff check src tests` – ≈154 pre-existing findings (mostly E501): do not mass-fix, keep new code clean |
| Frontend | `cd frontend && npm run dev` · `npm run build` (`tsc -b` + vite) · `npm run lint` |
| Run the app | `uv run python -m trading_signals.main` – needs the DB from `.env`. The local `.env` points at the **production** DB and the app starts the scheduler + startup catch-up → do not start it locally without Sebastian's OK |
| Migrations | `src/alembic/versions/` (001–036), applied by the container entrypoint on every start (fails hard). Do not run `alembic upgrade` against the local `.env`. A new migration is a new file `037_<name>.py` with `down_revision = "036"` ([DATABASE.md](docs/DATABASE.md)) |
| Test migrations on a fresh DB | Docker Desktop: `docker run -d --rm --name alpaca-migtest -e POSTGRES_USER=migtest -e POSTGRES_PASSWORD=migtest -e POSTGRES_DB=broker_data -p 127.0.0.1:55435:5432 postgres:18`, then with `DB_HOST=127.0.0.1 DB_PORT=55435 DB_NAME=broker_data DB_USER=migtest DB_PASSWORD=migtest` (set **all** `DB_*` – `.env` points at production): `.venv/Scripts/alembic.exe upgrade head` and `.venv/Scripts/alembic.exe check` (must print "No new upgrade operations detected."), finally `docker rm -f alpaca-migtest` |

**Production checks (read-only – the primary text channels):**

| What | How |
|---|---|
| Health | `curl -s http://192.168.1.93:8090/api/v1/health` → 200 `ok` / 503 `degraded` |
| Jobs + tables | `curl -s http://192.168.1.93:8090/api/v1/dashboard/summary` → `collectors[]` (`id`, `last_status`, `last_run`, `next_run`, `is_running`, `via_chain`), `table_stats[]` (`table`, `row_count`, `min_date`, `max_date`), `system_health` (`alembic_revision`, `job_count`, `scheduler_running`, `uptime_seconds`) |
| Run logs | `curl -s "http://192.168.1.93:8090/api/v1/logs?status=failed&limit=10"` – filters `status` (`failed`, `partial`, …), `collector` (job id or collector name), `page`, `limit` ≤ 200; entries carry `notes`, `errors`, `log_lines`; `/api/v1/logs/collectors` lists the names |
| Context packs | Share `\\HOMENAS\Workfiles\AlpacaBroker\context_packs\<session-date>\` (`00_uebersicht.md`, `NN_TICKER.md`, `decisions.yaml` – format: [STAGE2_DECISIONS.md](docs/STAGE2_DECISIONS.md)) and `context_packs\stage2_auswertung.md`; read-only, files are UTF-8 with BOM. Access by IP (`\\192.168.1.93`) is denied, use the hostname |

**Unraid actions:** Unraid WebGUI (http://192.168.1.93) in Claude Code's built-in browser pane; Sebastian logs in himself (never enter credentials). Everything runs through **one User Script `alpaca-ops`** (Settings → User Scripts → "Run Script" → argument dialog; "Run In Background" skips the dialog and always runs the default `status`):

| Argument | Effect |
|---|---|
| `status` | container state/health, deployed vs. fetched commit, disk, alpaca-related cron entries |
| `logs [N] [SINCE]` | last N container log lines (default 500, max 5000), e.g. `logs 2000 12h` |
| `deploy` / `deploy-log` | git pull → build → recreate, detached (guards: not 22:15–05:15, not while a job runs) / its progress |
| `restart` | container restart (same guards) |
| `run scripts/…py [args]` | project script inside the container as `99:100` (e.g. repair scripts) |

The wrapper fetches [infra/unraid/alpaca-ops.sh](infra/unraid/alpaca-ops.sh) from `origin/main` on every run – change the ops logic by pushing, never by editing the script in the WebGUI. Read results as text from `//HOMENAS/Workfiles/AlpacaBroker/ops/<command>.log` (Git Bash path), not from screenshots. Windows caches share files: a log that was rewritten in place (e.g. `deploy-worker.log`) can show stale content for minutes – trust `ops/deployed.txt`, file mtimes (`ls -la`) and the API over a stale log. The "Run Script" popup shows the live output (read it with a screenshot/zoom); during long runs check every ~30–60 s (popup, API `uptime_seconds`/`alembic_revision`/`is_running`/`last_status`) instead of blocking on one long wait loop (Sebastian, 2026-10-16). Deploy workflow: skill `/deploy`. Scope limits: see Safety rules.

## Working Agreements

- **Agent-based:** delegate substantial work packages (implementation, doc updates, code-vs-doc audits, reviews) to subagents with self-contained briefs; run independent packages in parallel; verify results yourself before committing. Trivial edits stay inline. Project agents: `.claude/agents/`; workflows: `.claude/skills/` (`/log-check`, `/deploy`, `/session-end`).
- **Model choice – always the cheapest fitting model, set `model` explicitly on every Agent call:**

  | Work | Model | Project agent |
  |---|---|---|
  | Lookups, log/health checks, mechanical verification | Haiku | `log-checker` |
  | Implementation, debugging, doc updates (default) | Sonnet | `doc-keeper` |
  | Architecture, edge cases, reviews – sparingly | Opus | `code-reviewer` |
  | Product runtime (collectors, scheduler, features) | no LLM | – |

  The product code uses no LLM today (sentiment = local FinBERT); a Haiku upgrade via API is only a planned option ([DECISIONS_FEATURES.md](docs/DECISIONS_FEATURES.md), 2026-05-13).
- **Autonomy (Sebastian, 2026-10-16: "so autonom wie möglich"):** work independently – decide, implement, test, commit, push and **deploy** finished work packages without asking (`/deploy`, guards in `alpaca-ops`). Ask Sebastian only for strategy/product decisions, the "Never without explicit confirmation" items and data-changing production actions (below).
- **Production data is protected:** GET API calls, share reads, `alpaca-ops status|logs|deploy-log` are always fine. Data-changing actions – `alpaca-ops run` of repair/backfill scripts with `--apply`, mutating API calls (`POST /ops/*`, `/analysis/*`; `POST /ops/db/reset` wipes all data tables) – only when Sebastian asked for them in this session. There are no DB backups.
- **Verify before commit:** `uv run pytest` green, `git diff` reviewed (no secrets, no `.env`), docs updated. Never force-push; never amend published commits.
- **Commits:** Conventional Commits in English (`feat|fix|docs|test|infra|refactor(scope): subject`, body explains why) plus the Co-Authored-By trailer the harness specifies. Single-developer repo, work happens on `main`.
- **Languages:** chat with Sebastian in German. Code, comments, commit messages, CLAUDE.md, ARCHITECTURE, DECISIONS, LEARNINGS in English. ROADMAP and SESSION_LOG recent entries in German – continue that way.

## Invariants & Gotchas

Each item is backed by `docs/DECISIONS_*.md`, `docs/LEARNINGS.md` or the code.

**Data correctness**
- **Market-session date labels:** use `utils/market_calendar.last_completed_session()`, never `date.today()` (the container runs in Berlin time). Snapshots and targets exist only for NYSE trading days.
- **Point-in-time / no lookahead** (DECISIONS_FEATURES, "Information Cut-offs"): a feature for day `d` may only use what was public by `d` – politicians `disclosure_date <= d`, 13F `filing_date <= d` (`put_call = 'SH'`), Form 4 public if `acceptance_datetime` < 16:00 ET on `d` else `filing_date <= d`, news `published_at` < 16:00 ET, earnings `first_seen <= d`, FINRA short interest `settlement_date` <= the 9th NYSE session before `d` (publication lag, `derived/short_interest_features.py`). Targets enter at `open(d+1)`. Universe via `get_universe_as_of(d)` (survivorship). `universe.sector` is not point-in-time (known limit).
- **Retention:** `data_start_date()` (analysis: `ml_start_date()`), never fixed start dates, never cached at import time (docstring of `utils/retention.py`); backfills must not collect before the window.
- **NULL vs 0:** `0` = source covers the ticker but nothing happened, `NULL` = no coverage. Extrapolated (gap-filled) prices are never used for TA, targets, liquidity or breadth.
- **Code hygiene:** `date(…) == pd.Timestamp(…)` is `False` – convert with `pd.Timestamp(d)` for DataFrame index lookups (LEARNINGS 2026-04-16). Every job writes a `collection_log` row (`BaseCollector` / `run_logged_job`); completeness checks use per-date ticker coverage, not `MAX(date)`; a catch-all `except` returning a sentinel (e.g. `row_count = -1`) needs a log line. Feature groups come from `analysis/feature_groups.group_features(FEATURE_COLUMNS)`; endpoints serving the same model share `api/serializers.py`.

**Tests**
- **Unit tests never open a DB session** (DECISIONS_ARCHITECTURE 2026-10-15). Code that calls `get_session()` internally – e.g. `NewTickerOnboarder` inside `PoliticianTradesCollector.store()` – must be patched (`patch("trading_signals.universe.onboarder.NewTickerOnboarder")`), otherwise the test talks to the production DB. Prove isolation with the unreachable-DB run above.

**Operations**
- **Migrations:** never change what an applied migration does (production is at 036) – schema changes go into a new migration. The only allowed edit of an applied migration (001–036) is making a migration idempotent so a fresh DB replays cleanly (precedent: 025, 2026-10-16 – 024 had been extended after production applied it). Test every migration on a fresh DB (command above) before deploying – a failing migration sends the container into a restart loop. The ORM must mirror the migrated schema: every schema change = new migration + matching ORM change in one commit, `alembic check` clean.
- **Container user `99:100`:** never `docker exec -u root` (root-owned files in `context_packs/` cannot be overwritten → `PermissionError`, LEARNINGS 2026-10-15); `/mnt/user/Workfiles/AlpacaBroker` must be owned by `99:100`.
- **Night window:** collectors 22:30–~03:00, `nightly_chain` 04:30 ([SCHEDULER.md](docs/SCHEDULER.md)). No deploy/restart between 22:15 and 05:15 or while any job has `is_running: true` (a restart kills runs; they are marked stale on the next start). No manual writes to `feature_snapshots` during the chain (04:30–05:00, deadlock risk).
- **Expected states:** weekend/holiday → `nightly_chain` `skipped` ("no new session"); IEX fallback or stale non-critical chain inputs → `partial`; critical input (prices stale, or < 80 % price coverage of the session vs. the max of the last 5 sessions) → chain `failed`, features + context pack skipped, notes carry the repair hint; earlier sessions with missing snapshots are rebuilt automatically by the next chain runs once their prices are complete (`catch-up: <date>=success|pending|failed`, runs on already processed targets are `catch-up only`); `row_count = -1` in `table_stats` is a bug sentinel. After a deploy hard-reload the browser (Ctrl+F5) before debugging the UI.
- **Paper only:** startup aborts if `ALPACA_ENDPOINT` does not contain `paper` (`Settings.validate_alpaca_safety`); the system places no orders.

## Project Documents

Full catalog: [docs/INDEX.md](docs/INDEX.md).

| Document | Purpose | Update Frequency |
|---|---|---|
| **CLAUDE.md** (this document) | Entry point, commands, working agreements, invariants | Rarely |
| [docs/INDEX.md](docs/INDEX.md) | **Documentation navigator** – find the right doc | Rarely |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Deployment, project structure, data flow, operations access | On structural changes |
| [docs/DATABASE.md](docs/DATABASE.md) | Complete DB schema (all tables + DDL) | On schema changes |
| [docs/SCHEDULER.md](docs/SCHEDULER.md) | Execution schedule (all jobs) | When jobs change |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Data source catalog | When new sources added |
| [docs/DECISIONS_ARCHITECTURE.md](docs/DECISIONS_ARCHITECTURE.md) | Decisions: infrastructure & architecture | On arch decisions |
| [docs/DECISIONS_DATA.md](docs/DECISIONS_DATA.md) | Decisions: data sources & collectors | On data decisions |
| [docs/DECISIONS_FEATURES.md](docs/DECISIONS_FEATURES.md) | Decisions: features, scoring, trading | On strategy decisions |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Sprint planning, status, open points | **After every sprint** |
| [docs/SESSION_LOG.md](docs/SESSION_LOG.md) | Chronological session history | **After every session** |
| [docs/LEARNINGS.md](docs/LEARNINGS.md) | Observed findings, debugging stories | Continuously |
| [docs/LEARNINGS_HYPOTHESES.md](docs/LEARNINGS_HYPOTHESES.md) | Hypotheses to test, planned investigations | As research evolves |

## Session Start Checklist

1. `git status` and `git log -5` – the tree should be clean (Antigravity no longer edits this repo, but check for stray changes).
2. Read the **last entry** of [SESSION_LOG.md](docs/SESSION_LOG.md) (bottom of the file) and the ROADMAP sections "Offene Punkte" and "Nächste Schritte".
3. Scan `DECISIONS_*.md` for the topic at hand; read the relevant chapter of ARCHITECTURE.md / DATABASE.md.
4. If the session touches production or follows a night run: `/log-check`. Clarify the goal with Sebastian only if the request is ambiguous – then work autonomously.

## Session End Checklist (`/session-end`)

1. `uv run pytest` green; `uv run ruff check` on changed files.
2. Documentation via the `doc-keeper` agent: ROADMAP (open points, next steps), SESSION_LOG (new entry), DECISIONS_* (new decisions), LEARNINGS (new findings), DATABASE.md (schema), SCHEDULER.md (jobs), ARCHITECTURE.md (structure), and the **Current Status** block above.
3. Review `git diff`, commit (Conventional Commits), push to `origin/main`.
4. If runtime code changed: `/deploy` (outside 22:15–05:15); afterwards update the **Deployment** line above.

---

## Important Boundaries & Safety Rules

### 🚨 Never Without Explicit Confirmation

- **Never** activate live trading (hardcoded check on `paper-api.alpaca.markets`)
- **Never** place real orders without manual approval
- **Never** commit credentials to Git (`.env` files in `.gitignore`)
- **Never** touch the GynOrg database (strict separation)

### Unraid WebGUI scope

In the WebGUI only the container `alpaca-broker`, its folders (`/mnt/user/appdata/alpaca-broker`, `/mnt/user/Workfiles/AlpacaBroker`) and its own User Scripts may be touched. **Never** other containers (GynOrg! – this includes the PostgreSQL container), and never array, shares, users, network or security settings. Sebastian logs in himself; Claude never types credentials.

### Responsibility

This project is **private and experimental**. All decisions are made by Sebastian. Claude assists, implements, and advises – but final responsibility for every trade and configuration lies with the human.

---

## Relationship to Other Projects

Sebastian works on other projects in parallel. This project is **strictly separated** from:
- **GynOrg** (gynecological clinic management, own PostgreSQL)
- **WoSZ** (Wardens of Sector Zero, strategy game concept)
- **Other coding experiments**

Trading Signals has its own folder, its own DB, its own Docker Compose.
