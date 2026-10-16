# DECISIONS_ARCHITECTURE.md – Architecture & Infrastructure Decisions

> Decisions related to infrastructure, deployment, tooling, UI, and observability.
>
> See also: [INDEX.md](INDEX.md) · Related: [DECISIONS_DATA.md](DECISIONS_DATA.md) · [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md)

**Last updated:** 16 October 2026

---

### [2026-04-11] Database: Separate PostgreSQL 18 Container

**Decision:** Separate container (not shared with GynOrg).
**Rationale:** Strict separation between medical and trading data. Independent backups.
**Revisit trigger:** Never – non-negotiable.

---

### [2026-04-11] Architecture: Deterministic Core, LLM Only at the Edges

**Decision:** Core code deterministic. LLMs only for unstructured tasks (news parsing, reports, ad-hoc analysis).
**Rationale:** Trading decisions must not depend on hallucinations. Unit tests require determinism. Cost-optimal.

---

### [2026-04-11] Data Storage: Append-only Raw Layer + Computed Derived Layer

**Decision:** Two-layer model. Raw layer is append-only and immutable. Derived layer recomputed on demand.
**Rationale:** If computation model changes, we can fully recompute. Raw data is Single Source of Truth.

---

### [2026-04-11] Broker: Alpaca Paper Trading, Never Live

**Decision:** Alpaca Paper Trading with hardcoded safety check: `assert "paper" in settings.ALPACA_ENDPOINT`.
**Revisit trigger:** Only after 12+ months successful paper trading.

---

### [2026-04-11] Documentation Strategy: CLAUDE.md + docs/ Folder

**Decision:** `CLAUDE.md` as entry point, `docs/` folder for detail documents. Everything in Git.

---

### [2026-04-12] Technical Decisions Batch (9 decisions resolved)

1. ~~Politician trades: Quiver API~~ → Superseded (Senate eFD, see DECISIONS_DATA)
2. **TA Library:** pandas-ta (pure Python, no C compiler)
3. **Package Manager:** uv (10–100x faster than Poetry)
4. **Scheduler:** APScheduler (shared process with FastAPI, dynamic control)
5. **Monitoring:** Telegram + Email fallback
6. **Dashboard:** FastAPI + Vite/React SPA (Stitch Design System)
7. Feature Selection → see [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md)
8. Scoring Model → see [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md)
9. Portfolio Construction → see [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md)

---

### [2026-04-12] DB Connection: Adapting to Existing Infrastructure

**Decision:** Container `postgresql18-alpaca`, DB `broker_data`, user `sebastian`, port 5435, volume `/mnt/user/Datafolder/Broker/`. Schema `signals`.

---

### [2026-04-13] FastAPI + Vite/React SPA Instead of Streamlit

**Context:** ROADMAP originally mentioned Streamlit, but the Stitch "Precision Architect" Design System (Dark Mode, Cyan Primary, glassmorphism) is not achievable in Streamlit.
**Decision:** FastAPI + Vite/React SPA. Static files served by FastAPI.

---

### [2026-04-13] Single Container: Collector + API + UI

**Decision:** Everything in one container on port 8090. Multi-stage Docker build (Node → Frontend → Python → Runtime).
**Rationale:** Simplest Unraid deployment. FastAPI shares process with APScheduler → direct scheduler state access.

---

### [2026-04-13] Real-time Backfill Progress

**Decision:** Per-batch progress updates with ETA estimation. Frontend polls every 2s. Thread-safe via Lock.

---

### [2026-04-13] Factory Reset: Delete Data, Not Drop Schema

**Decision:** `DELETE FROM` all data tables in transaction. Universe preserved. Respects FK order.

---

### [2026-04-13] Monthly Index Sync

**Decision:** Monthly on 1st, 03:00 CET. Automatic sector enrichment for new tickers.

---

### [2026-04-13] SPA Routing: Catch-All Fallback

**Decision:** FastAPI catch-all `@app.get("/{full_path:path}")` serving `index.html` for non-API paths.

---

### [2026-04-13] Live Job Status via APScheduler Event Listener

**Decision:** `JobTracker` with `EVENT_JOB_SUBMITTED/EXECUTED/ERROR`. In-memory tracker. Dashboard polls 5s.

---

### [2026-04-13] Data Quality Tile: Live Computation

**Decision:** Live computation in API endpoint (4 COUNT/MAX queries). No DB table needed.

---

### [2026-04-13] Sector/Industry: yfinance Enrichment

**Context:** ~740/845 tickers had no sector. Alpaca Assets API doesn't provide sector data.
**Decision:** yfinance `ticker.info` for enrichment. Integrated into monthly index sync.

---

### [2026-04-15] Central NewTickerOnboarder

**Decision:** Central `NewTickerOnboarder` in `universe/onboarder.py`. DRY onboarding pipeline: Blacklist → Alpaca → quoteType → Backfill (Prices → TA → Fundamentals → Sector).

---

### [2026-04-15] In-Process Log Capture (not Docker Log API)

**Decision:** `CollectorLogCapture` context manager. Captures WARNING+ and collector-specific INFO. Ring buffer (200 lines). Stored in `collection_log.log_lines` JSONB.

---

### [2026-04-15] API Universe Filter: Active-Only by Default

**Decision:** API defaults to `is_active = true`. Override with `?active=false`.

---

### [2026-04-14] Collection Log `notes` Field

**Decision:** `notes` TEXT column in `collection_log`. Makes silent failures visible without container logs.

---

### [2026-04-16] TA Job: Observability via CollectorLogCapture

**Decision:** Integrated into existing CollectorLogCapture pattern. All jobs use same observability.

---

### [2026-05-13] Feature Coverage Endpoint: Latest Snapshot Day Only

**Decision:** `GET /features/coverage` aggregates only the latest `snapshot_date` (not the history) to keep the ticker × feature-group matrix cheap. *(Origin: Sprint 8b plan, see ROADMAP.)*

---

## Review 2026-10-05 and Follow-ups (Operations)

### [2026-10-05] Nightly Chain Instead of Fixed-Time Derived Jobs

**Context:** Feature pipeline, target backfill and context pack ran at fixed times and could start before upstream collectors had finished (or on the wrong date label).
**Decision:** One `nightly_chain` job (04:30 Europe/Berlin) runs TA catch-up → `feature_pipeline` → `target_backfill` → `stage2_review` → `context_pack_generator` for the **last completed NYSE session**. It waits for running upstream collectors (`scheduler/registry.NIGHTLY_UPSTREAM`; prices are critical). The chain steps stay registered as **paused** jobs so they remain visible and manually triggerable. Job definitions live in `scheduler/setup.py`, job ↔ collector names in `scheduler/registry.py`.

### [2026-10-05] Market-Session Date Labels

**Decision:** All collectors and derived jobs label data with `utils/market_calendar.last_completed_session()` instead of the Berlin calendar date. Context-pack folders are named after the market session, not after the run date.

### [2026-10-05] Non-Root Container (UID 99 / GID 100)

**Decision:** The runtime image runs as `99:100` (Unraid `nobody:users`), matching the ownership of `/mnt/user/Workfiles`. `/app` is owned by the app user (`64089e2`).
**Consequence:** The host directory `/mnt/user/Workfiles/AlpacaBroker` must be owned by `99:100`. Files created by `docker exec -u root …` cannot be overwritten later by the app (cause of the context-pack `PermissionError` on 2026-10-05/06). Plain `docker exec` already runs as `99:100`.

### [2026-10-05] Write Protection for Mutating Endpoints

**Decision:** `POST` endpoints under `/ops/*` and `/analysis/*` depend on `api/deps.require_write_access`: header `X-API-Key` must match `API_KEY` if configured, otherwise at least `X-Requested-With` must be present (CSRF guard). The UI stores the key under *Einstellungen*.

### [2026-10-05] Alerting and Startup Catch-up

**Decision:** `partial`/`failed` runs and APScheduler error/missed events are pushed to `ALERT_WEBHOOK_URL` (ntfy-compatible, optional; `utils/alerting.py`). Because the job store is in-memory, `registry.CATCHUP_JOBS` re-runs `price_collector`, `estimates_collector` and `nightly_chain` shortly after a restart if their last scheduled run was missed.

### [2026-10-15] Target Backfill Retries on Deadlock

**Context:** On 2026-10-05 a manually triggered chain run hit a PostgreSQL deadlock (SQLSTATE 40P01) against an untracked concurrent writer on `feature_snapshots`.
**Decision:** `_target_backfill_body` retries up to 3 times (5 s / 15 s backoff) on 40P01 only (`97fcdb0`). Safe because `backfill_all` only processes rows whose targets are still NULL.
**Operational rule:** Do not start manual scripts that write `feature_snapshots` during the nightly chain (≈ 04:30–05:00).

### [2026-10-15] Development Tooling: Claude Code Instead of Antigravity

**Context:** Development so far happened in the IDE Antigravity; on 2026-10-15 the project was handed over to Claude Code (Claude desktop app). Antigravity's final documentation commit is `e09cc90` (re-checked against the code, see SESSION_LOG Session 32).
**Decision (Sebastian):** Further development happens in Claude Code; Antigravity no longer edits this repo. The repo documentation remains the only project memory – no tool-specific artifacts are needed.
**Rationale:** The project memory already lives entirely in the repo docs. Claude Code loads `CLAUDE.md` automatically at the start of every session, so the existing session start checklist applies unchanged. One tool per working tree avoids conflicting edits.
**Consequence:** Unit tests must not need a database (since Session 32 none does; checked with the DB deliberately unreachable). Claude Code has no SSH access to Unraid, so the Unraid deploy remains a manual step for Sebastian.
**Tooling notes (takeover assessment):** The MCP servers used in Antigravity (context7, StitchMCP, NotebookLM, ObsidianUnraid) are not set up in Claude Code; add them (`claude mcp add` / `.mcp.json`) only when needed – Stitch only for UI design work. Locally, `ruff` is available only inside the venv (`uv run ruff`), and there is no `psql` (database queries via Python/SQLAlchemy or Docker).

### [2026-10-16] Unraid Operations via WebGUI and One User Script (No SSH)

**Context:** To work autonomously Claude Code needs to deploy, read container logs and run project scripts on the Unraid host. SSH is disabled on the server; a restricted forced-command SSH key was prepared but not installed.
**Decision (Sebastian):** Claude works on Unraid through the WebGUI in Claude Code's built-in browser – Sebastian logs in himself. Host actions run through **one** User Script `alpaca-ops` whose argument is the command (`status`, `logs`, `deploy`, `deploy-log`, `restart`, `run scripts/…py`). Deploys of finished, tested, pushed work may be run autonomously; data-changing actions (repair scripts with `--apply`, mutating `/ops/*` calls) only on request.
**Implementation:** The User Script is a 10-line wrapper that fetches [infra/unraid/alpaca-ops.sh](../infra/unraid/alpaca-ops.sh) from `origin/main` (`git show`, working tree untouched) – the ops logic is versioned and changes by push, the deployed app only by `deploy`. Every run mirrors its output to `Workfiles/AlpacaBroker/ops/<command>.log`, which Claude reads as text via `\\HOMENAS\Workfiles` instead of interpreting terminal screenshots. Guards in the script: no deploy/restart 22:15–05:15 or while a job has `is_running: true`; deploy builds before recreating (old container keeps serving), detached with a lock.
**Scope:** in the WebGUI only `alpaca-broker`, its appdata/Workfiles folders and its own User Script – never other containers (GynOrg, other projects on the host), array, shares, users, network or security settings (CLAUDE.md, "Unraid WebGUI scope").
**Trade-off:** the wrapper runs repo code as root on the host, so anyone who can push to `main` can act on the host when the script is run – acceptable for a single-developer private repo; the previous 2026-10-15 note "deploy remains a manual step" is superseded.

---

## Pending Decisions

New architectural decisions will be added here as they arise.
