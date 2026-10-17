---
name: deploy
description: Deploy the current main branch to the production container on Unraid via the User Script "alpaca-ops" (argument "deploy") in the Unraid WebGUI in Claude's built-in browser, then verify through the production API and record the deploy in CLAUDE.md and SESSION_LOG. Use after finished, tested and pushed runtime changes, or when Sebastian asks for a deploy.
---

# Deploy to Unraid

The container is rebuilt from `origin/main`: `git pull` on the server → `docker compose build` (~7–8 min, the old container keeps serving) → `docker compose up -d` (container recreated; Alembic migrations run in the entrypoint and **fail hard**). Sebastian allowed autonomous deploys of finished work (CLAUDE.md, "Autonomy"); the guards below are not optional.

Access path: Unraid WebGUI (http://192.168.1.93) in Claude Code's built-in browser pane (`mcp__Claude_Browser__*` tools). Sebastian logs in himself – never type credentials. In the WebGUI touch only the User Script `alpaca-ops`; scope rules are in CLAUDE.md ("Unraid WebGUI scope"). Never edit the script in the WebGUI – its logic is [infra/unraid/alpaca-ops.sh](../../../infra/unraid/alpaca-ops.sh), fetched from `origin/main` on every run.

## 1. Preconditions (all must hold, otherwise stop and report)

1. Working tree clean: `git status` shows nothing to commit.
2. Pushed: `git fetch origin`, then `git rev-parse HEAD` equals `git rev-parse origin/main`.
3. `uv run pytest` is green (note the count). If the diff since the deployed commit contains a migration or a change to collectors, features, targets or the scheduler and it has not been reviewed yet, run the `code-reviewer` agent first; with a migration also run the fresh-DB test from CLAUDE.md (`upgrade head` + `alembic check` clean) – a failing migration sends the new container into a restart loop.
4. Timing, from the production API (`curl -s http://192.168.1.93:8090/api/v1/dashboard/summary`) and `date`:
   - local Berlin time is **outside 22:15–05:15** (collectors 22:30–~03:00, nightly chain 04:30);
   - **no collector has `is_running: true`** (includes the Sunday daytime jobs `form13f_collector` 10:00, `politician_trades_collector` 11:00, `short_interest_positions_collector` 12:00 and `universe_validation` 13:00);
   - **no project script runs in the container** (a detached `alpaca-ops run-bg`, e.g. the survivorship feature rebuild, or a `run` in a popup): check `alpaca-ops run-log` or `status` (section "project scripts running in the container") first – a recreate would kill the script halfway;
   - preferably no `next_run` within the next ~15 minutes (the container is recreated at the end of the build; a restart kills running jobs, they are marked stale on the next start).
   `alpaca-ops deploy` enforces the first three itself and refuses otherwise (the script guard is re-checked right before the container is recreated: image built, recreate skipped, run deploy again later).
5. Record the "before" state: `system_health.alembic_revision`, `job_count`, `uptime_seconds`, the local `git rev-parse --short HEAD` and the newest migration number in `src/alembic/versions/`.

## 2. Run the deploy

1. Navigate the built-in browser to `http://192.168.1.93/Settings/Userscripts`. If the login page appears, ask Sebastian to log in (check with `tabs_context` that the pane is displayed first) and wait.
2. Click **Run Script** in the `alpaca-ops` row (first row). In the dialog "Enter In Script Arguments" replace the default `status` with `deploy` and confirm with **OK**. Do not run any other script.
3. Read `//HOMENAS/Workfiles/AlpacaBroker/ops/deploy.log` (Git Bash): it must say `deploy started in background`. If it shows `ERROR: …` (time window, running job, lock), stop and report.
4. Follow the build: read `//HOMENAS/Workfiles/AlpacaBroker/ops/deploy-worker.log` every 1–2 minutes (text, no screenshots needed) until it ends with `deploy finished` or `ERROR`. Windows may serve a cached old copy of that log – if it does not change, check `ops/deployed.txt` (written at the end of a successful deploy) and the API `uptime_seconds` instead (a cached build typically finishes in ~2 min, a full one in ~5–7 min). It prints the pulled commit. Alternatively run `alpaca-ops` with `deploy-log`.

## 3. Verify (production API, read-only)

Poll `curl -s -m 10 http://192.168.1.93:8090/api/v1/health` until HTTP 200 (start waits for the DB and runs migrations; the Docker healthcheck has a 120 s start period). Then check against the "before" state:

- `health`: `status: ok`, `scheduler_running` and `db_connected` true.
- `system_health.alembic_revision` equals the newest migration number in `src/alembic/versions/`.
- `system_health.job_count` equals the number of jobs in `docs/SCHEDULER.md` (26 at the time of writing).
- `system_health.uptime_seconds` is small (new container).
- `table_stats[]`: no `row_count < 0`.
- `curl -s "http://192.168.1.93:8090/api/v1/logs?status=failed&limit=5"`: no new failed run started after the deploy.
- Run `alpaca-ops` with `status` and read `ops/status.log`: `deployed:` equals the local HEAD short hash, container `running health=healthy`.
- UI changes: hard-reload the browser (Ctrl+F5) before judging the SPA.

## 4. Record

1. Update the **Deployment** line in CLAUDE.md (commit, date, Alembic revision) and, if the status block changed, Phase/Next step.
2. Add a SESSION_LOG entry (German, via the `doc-keeper` agent or inline): commit range deployed, build result, API verification, anything unexpected. Tick the matching ROADMAP items ("nicht deployt" notes).
3. Commit `docs: record deploy of <hash>` and push (docs-only, no new deploy needed).

## 5. If something fails

- Do not retry blindly and do not "fix" things on the server. Report the tail of `ops/deploy-worker.log` to Sebastian verbatim, together with the API state (`health`, `system_health`).
- **Build failed:** the previous container keeps running (build happens before `up -d`); confirm with `health` and `uptime_seconds`.
- **Migration failed / container restarting:** `health` is 503 or unreachable. Run `alpaca-ops` with `logs 300` and read `ops/logs.log`. Rolling back means reverting the commit and deploying again (migrations are forward-only; never edit an applied migration) – decide that with Sebastian.
- Never touch other containers, the database container, or any Unraid setting while troubleshooting.
