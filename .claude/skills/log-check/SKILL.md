---
name: log-check
description: Daily operations check ("Log-Check") of the production Signal Warehouse. Delegates to the read-only log-checker agent (Haiku) - API health, dashboard summary, failed/partial runs, context pack and stage-2 report - and summarizes the result for Sebastian in German. Use after the nightly run, at session start, or when asked for a log or health check.
---

# Log-Check

Read-only check of production. Optional argument: a start time ("since", e.g. `2026-10-16T04:00`); without it the check covers the last night run (yesterday 22:00 Berlin time onwards).

Arguments: $ARGUMENTS

## Steps

1. **Delegate.** Start the project agent `log-checker` (Agent tool, `subagent_type: log-checker`, `model: haiku`). Brief it with the since time (from the arguments, else yesterday 22:00), the current date/time and weekday, and any focus Sebastian named. It needs nothing else: it knows the API endpoints and the share path.
2. **Do not relay blindly.** Haiku reports confidently. For every item it flags as `failed`, stale, negative row count, or a missing context pack, spot-check once yourself with a single read-only call (for example `curl -s "http://192.168.1.93:8090/api/v1/logs?collector=<name>&limit=3"`, or listing the share folder) before you present it as a finding.
3. **Classify.** Separate *expected* states (weekend/holiday -> `nightly_chain` `skipped`, `partial` from the IEX fallback, `short_interest` empty until its initial backfill ran, no `decisions.yaml` yet) from *real* findings. Rules and thresholds are in `.claude/agents/log-checker.md` and CLAUDE.md ("Invariants & Gotchas").
4. **Report to Sebastian in German**, briefly: verdict (OK / Auffälligkeiten / Fehler), the real findings with cause and evidence, and your recommended next step. Do not paste raw JSON.
5. **Follow-up.**
   - Findings that need a code fix: handle as normal development work (tests, review, commit). Deploying the fix only on Sebastian's request (`/deploy`).
   - No production-changing action (restart, trigger, deploy, `POST /ops/*`) without his request.
   - A clean check needs no SESSION_LOG entry; notable findings go into SESSION_LOG/LEARNINGS during `/session-end`.
