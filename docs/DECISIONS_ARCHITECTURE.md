# DECISIONS_ARCHITECTURE.md – Architecture & Infrastructure Decisions

> Decisions related to infrastructure, deployment, tooling, UI, and observability.
>
> See also: [INDEX.md](INDEX.md) · Related: [DECISIONS_DATA.md](DECISIONS_DATA.md) · [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md)

**Last updated:** May 2026

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

## Pending Decisions

New architectural decisions will be added here as they arise.
