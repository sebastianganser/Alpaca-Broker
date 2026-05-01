# Documentation Index – Alpaca-Broker

> Central navigator for all project documentation.
> Start here to find the right document for your question.

**Last updated:** May 2026

---

## Quick Reference by Question

| I'm looking for... | Document |
|---|---|
| Project overview, mission, principles | [CLAUDE.md](../CLAUDE.md) |
| How is the system deployed and structured? | [ARCHITECTURE.md](ARCHITECTURE.md) |
| What database tables exist? (DDL, schema) | [DATABASE.md](DATABASE.md) |
| When do jobs run? (schedule, timing) | [SCHEDULER.md](SCHEDULER.md) |
| How does data source X work? | [DATA_SOURCES.md](DATA_SOURCES.md) |
| Why was decision Y made? | `DECISIONS_*.md` (see below) |
| What's the current sprint status? | [ROADMAP.md](ROADMAP.md) |
| What happened in session Z? | [SESSION_LOG.md](SESSION_LOG.md) |
| What did we learn from the data? | [LEARNINGS.md](LEARNINGS.md) |
| What hypotheses do we want to test? | [LEARNINGS_HYPOTHESES.md](LEARNINGS_HYPOTHESES.md) |
| GitHub overview / external audience | [README.md](../README.md) |

---

## Complete Document Catalog

### Core Documents

| Document | Purpose | Update Frequency |
|---|---|---|
| [CLAUDE.md](../CLAUDE.md) | Entry point for every AI session – project identity, principles, session checklists | Rarely |
| [README.md](../README.md) | GitHub-facing project overview | After major milestones |

### Architecture & Infrastructure

| Document | Purpose | Update Frequency |
|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Deployment topology, project structure, data flow diagrams | On structural changes |
| [DATABASE.md](DATABASE.md) | Complete database schema – all tables with DDL, layer organization | On schema changes / migrations |
| [SCHEDULER.md](SCHEDULER.md) | Execution schedule – daily, weekly, monthly, manual jobs | When jobs change |

### Data Sources

| Document | Purpose | Update Frequency |
|---|---|---|
| [DATA_SOURCES.md](DATA_SOURCES.md) | Catalog of all data sources with API details, limitations, implementation notes | When new sources are added |

### Decision Log (split by domain)

| Document | Scope | Update Frequency |
|---|---|---|
| [DECISIONS_ARCHITECTURE.md](DECISIONS_ARCHITECTURE.md) | Infrastructure, deployment, tooling, UI, observability | On architectural decisions |
| [DECISIONS_DATA.md](DECISIONS_DATA.md) | Data sources, collectors, data quality, universe management | On data-related decisions |
| [DECISIONS_FEATURES.md](DECISIONS_FEATURES.md) | Feature engineering, scoring, trading strategy, ML pipeline | On strategy decisions |

### Progress & History

| Document | Purpose | Update Frequency |
|---|---|---|
| [ROADMAP.md](ROADMAP.md) | Sprint definitions, status overview, deployment notes, backlog | After every sprint |
| [SESSION_LOG.md](SESSION_LOG.md) | Chronological record of what happened in each session | After every session |

### Learnings & Research

| Document | Purpose | Update Frequency |
|---|---|---|
| [LEARNINGS.md](LEARNINGS.md) | Observed findings, debugging stories, data quality discoveries | Continuously |
| [LEARNINGS_HYPOTHESES.md](LEARNINGS_HYPOTHESES.md) | Hypotheses to test, planned investigations, failed approaches | As research evolves |

---

## Session End Checklist

After every productive session, update these documents as needed:

1. ✅ `ROADMAP.md` – Update sprint status
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
- **No sprint-specific files** – Sprint plans are integrated into `ROADMAP.md` and `SESSION_LOG.md`
