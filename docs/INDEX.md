# Documentation Index – Alpaca-Broker

> Central navigator for all project documentation.
> Start here to find the right document for your question.

**Last updated:** 17 October 2026 (S&P 500 survivorship repair finished; three re-run reports of 2026-10-17 added)

---

## Quick Reference by Question

| I'm looking for... | Document |
|---|---|
| Project overview, mission, principles, current status | [CLAUDE.md](../CLAUDE.md) |
| **Where did the last session stop? What is deployed? What is open?** | [SESSION_LOG.md](SESSION_LOG.md) – last entry (Session 34) · [ROADMAP.md](ROADMAP.md) – "Offene Punkte" |
| How is the system deployed and structured? | [ARCHITECTURE.md](ARCHITECTURE.md) |
| What database tables exist? (DDL, migrations 001–038, retention) | [DATABASE.md](DATABASE.md) |
| When do jobs run? (schedule, nightly chain) | [SCHEDULER.md](SCHEDULER.md) |
| How does data source X work? | [DATA_SOURCES.md](DATA_SOURCES.md) |
| Why was decision Y made? | `DECISIONS_*.md` (see below) |
| What's the current sprint status? | [ROADMAP.md](ROADMAP.md) |
| What did we learn from the data / from bugs? | [LEARNINGS.md](LEARNINGS.md) |
| What hypotheses do we want to test? | [LEARNINGS_HYPOTHESES.md](LEARNINGS_HYPOTHESES.md) |
| Short-term candidate concept (barrier trades, K1–K5) | [2026-10-06_Konzept_Kurzfrist_Kandidaten.md](2026-10-06_Konzept_Kurzfrist_Kandidaten.md) |
| Format of the stage-2 feedback file `decisions.yaml` (historical, valid until the forecast pipeline is deployed) | [STAGE2_DECISIONS.md](STAGE2_DECISIONS.md) |
| Format of Claude's forecast file `forecasts.yaml` and the pre-registered evaluation of the forecasts | [FORECASTS.md](FORECASTS.md) |
| Candidate screen: quality gate, evidence groups with literature signs, buckets, backtest | [SCREEN.md](SCREEN.md) |
| S&P 500 survivorship gap: root cause, removal list, **repair runbook** (dry runs, `--apply`, rollback) | [reports/2026-10-16_sp500_survivorship_inventory.md](reports/2026-10-16_sp500_survivorship_inventory.md) |
| GitHub overview / external audience | [README.md](../README.md) |

---

## Complete Document Catalog

### Core Documents

| Document | Purpose | Update Frequency |
|---|---|---|
| [CLAUDE.md](../CLAUDE.md) | Entry point for every AI session – project identity, principles, status, quick facts, session checklists | Status after every session |
| [README.md](../README.md) | GitHub-facing project overview | After major milestones |
| [.claude/](../.claude/) | Shared Claude Code setup: subagents (`log-checker` Haiku, `doc-keeper` Sonnet, `code-reviewer` Opus), skills (`/log-check`, `/deploy`, `/session-end`), permissions (`settings.json`) | When the workflow changes |
| [infra/unraid/alpaca-ops.sh](../infra/unraid/alpaca-ops.sh) | Host operations (status, logs, deploy, run, run-bg) behind the Unraid User Script `alpaca-ops` | When ops change |

### Architecture & Infrastructure

| Document | Purpose | Update Frequency |
|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Deployment topology (Unraid, container, volumes), project structure, data flow, nightly chain, context-pack output | On structural changes |
| [DATABASE.md](DATABASE.md) | Database schema – all tables, migration history 001–038, retention per table | On schema changes / migrations |
| [SCHEDULER.md](SCHEDULER.md) | Execution schedule – all 26 jobs, nightly chain, manual operations | When jobs change |

### Data Sources

| Document | Purpose | Update Frequency |
|---|---|---|
| [DATA_SOURCES.md](DATA_SOURCES.md) | Catalog of all data sources with API details, limitations, implementation notes | When new sources are added |

### Decision Log (split by domain)

| Document | Scope | Update Frequency |
|---|---|---|
| [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) | Infrastructure, deployment, tooling, UI, observability, operations (nightly chain, non-root container, alerting) | On architectural decisions |
| [DECISIONS_DATA.md](DECISIONS_DATA.md) | Data sources, collectors, data quality, universe management, retention (20 quarters) | On data-related decisions |
| [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md) | Feature engineering, targets, point-in-time rules, analysis methodology, trading goal | On strategy decisions |

### Progress & History

| Document | Purpose | Update Frequency |
|---|---|---|
| [ROADMAP.md](ROADMAP.md) | Sprint definitions, status overview, open points, next steps, deployment notes, backlog | After every sprint / session |
| [SESSION_LOG.md](SESSION_LOG.md) | Chronological record of every session, incl. handover (Session 31) and takeover in Claude Code (Session 32) | After every session |

### Learnings & Research

| Document | Purpose | Update Frequency |
|---|---|---|
| [LEARNINGS.md](LEARNINGS.md) | Observed findings, debugging stories, data quality discoveries, research results | Continuously |
| [LEARNINGS_HYPOTHESES.md](LEARNINGS_HYPOTHESES.md) | Hypotheses to test, planned investigations, failed approaches | As research evolves |

### Concepts (dated)

| Document | Purpose | Status |
|---|---|---|
| [2026-08-18_Konzept_Datenerweiterung.md](2026-08-18_Konzept_Datenerweiterung.md) | Concept for data extension and candidate pipeline (basis of Sprint 9.5a–c); answers and decisions are in DECISIONS_*, ideas not yet implemented in the ROADMAP backlog | Status per item in ROADMAP (9.5a–c) |
| [2026-10-06_Konzept_Kurzfrist_Kandidaten.md](2026-10-06_Konzept_Kurzfrist_Kandidaten.md) | Short-term candidates: barrier evaluation, pre-registered steps A–D, stage-1 risk pre-filter, stage-2 forward test (§7.7) | Historical – superseded by the strategy-neutral rework (valid until the forecast pipeline is deployed) |
| [2026-10-17_Plan_Strategieneutral.md](2026-10-17_Plan_Strategieneutral.md) | Plan to make Alpaca-Broker strategy-neutral after Sebastian's scope clarification: context pack v2 for Claude's Wall-Street-style forecasts, `forecasts.yaml`, neutral screen, removal of the barrier label and stage-2 tables | Approved 2026-10-17 – implementation running (steps 1–2 done, see ROADMAP) |
| [STAGE2_DECISIONS.md](STAGE2_DECISIONS.md) | File format `decisions.yaml` written by the analysis skill, read by `stage2_review` | Historical – valid until the forecast pipeline is deployed |
| [FORECASTS.md](FORECASTS.md) | File format `forecasts.yaml` (`forecasts/v1`, absolute direction per ticker and horizon), pre-registered evaluation (absolute and relative), decisive criterion (pre-registered 2026-10-18, 300 sessions) | Design 2026-10-17; ingest in plan step 4 |
| [SCREEN.md](SCREEN.md) | Neutral candidate screen: quality gate, 12 evidence groups with a-priori literature signs, event triggers, buckets (option D), historical validation | Implemented 2026-10-17 (`analysis/screen.py`); used by the context pack in plan step 3 |

### Reports (`docs/reports/`, generated 2026-10-06, 2026-10-16 and 2026-10-17)

| Document | Content |
|---|---|
| [reports/2026-10-06_hit_model_walkforward.md](reports/2026-10-06_hit_model_walkforward.md) | Step B: walk-forward hit-probability model (failed, AUC ≈ 0.50); pre-repair version |
| [reports/2026-10-06_event_study_buyback.md](reports/2026-10-06_event_study_buyback.md) | Step D1: 8-K buyback authorizations (failed) |
| [reports/2026-10-06_event_study_index_add.md](reports/2026-10-06_event_study_index_add.md) | Step D2: index additions (exploratory hint); pre-repair version |
| [reports/2026-10-06_buyback_audit.md](reports/2026-10-06_buyback_audit.md) | Audit sample of the buyback sentence classifier |
| [reports/2026-10-06_buyback_events.csv](reports/2026-10-06_buyback_events.csv) | Raw event list of the buyback study (data file) |
| [reports/2026-10-16_sp500_survivorship_inventory.md](reports/2026-10-16_sp500_survivorship_inventory.md) | S&P 500 survivorship inventory (read-only): root cause, reconciliation of Wikipedia vs GitHub, state per category, effort estimate, open questions; section 8 = repair runbook with expected numbers, verification SQL, rollback and the execution log of 2026-10-17 |
| [reports/2026-10-16_sp500_removals.csv](reports/2026-10-16_sp500_removals.csv) | One row per S&P 500 removal/rename 2021-01-01 … 2026-10-16 (data file of the inventory) |
| [reports/2026-10-17_hit_model_walkforward.md](reports/2026-10-17_hit_model_walkforward.md) | Step B re-run on the repaired data (557,275 snapshots): still not passed, holdout AUC ≈ 0.50 |
| [reports/2026-10-17_event_study_index_add.md](reports/2026-10-17_event_study_index_add.md) | Step D2 re-run on the repaired data: 110 events, −0.320 % vs. universe (unchanged) |
| [reports/2026-10-17_barrier_backtest.md](reports/2026-10-17_barrier_backtest.md) | K1 barrier backtest of the current context-pack score on the repaired data (1,060 days): approx. random, no significantly positive net return |
| [reports/2026-10-17_screen_backtest.md](reports/2026-10-17_screen_backtest.md) | Historical validation of the strategy-neutral screen `screen/v1` (raw, vs universe, vs SPY; 20/60 sessions); 60d: bullish +1.65 % vs universe (CI > 0), bullish minus bearish +4.13 % (CI > 0) – partly in-sample (short interest, insider) and before 2026 only 6 of 12 groups had data |

### Other READMEs

| Document | Purpose |
|---|---|
| [notebooks/README.md](../notebooks/README.md) | Sprint 9 analysis notebooks (setup, usage) |
| [scripts/archive/README.md](../scripts/archive/README.md) | Archived one-off scripts (not maintained, not in the Docker image) |

---

## Session End Checklist

After every productive session, update these documents as needed:

1. ✅ `ROADMAP.md` – Update sprint status, open points
2. ✅ `SESSION_LOG.md` – Document the session
3. ✅ `DECISIONS_*.md` – New decisions in the appropriate file
4. ✅ `LEARNINGS.md` – New findings (if applicable)
5. ✅ `DATABASE.md` – If new tables or migrations were added
6. ✅ `SCHEDULER.md` – If jobs were added or changed
7. ✅ `ARCHITECTURE.md` – If the project structure changed

---

## Naming Conventions

- **UPPERCASE.md** – Permanent, living documents
- **Prefix grouping** – Related documents share a prefix (`DECISIONS_*`, `LEARNINGS_*`)
- **`YYYY-MM-DD_*.md`** – Dated concept documents (snapshot of a design at that date)
- **`docs/reports/`** – Generated analysis reports (dated)
- **No sprint-specific living files** – Sprint plans are integrated into `ROADMAP.md` and `SESSION_LOG.md`
