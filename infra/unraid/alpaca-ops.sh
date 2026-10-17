#!/bin/bash
# =============================================================================
# Alpaca-Broker – operations script for the Unraid host
# =============================================================================
# Run through ONE Unraid User Script named "alpaca-ops" (Settings → User
# Scripts). That wrapper fetches this file from origin/main – without touching
# the working tree – and passes its argument string on:
#
#   APP=/mnt/user/appdata/alpaca-broker
#   git -C "$APP" fetch -q origin main
#   git -C "$APP" show origin/main:infra/unraid/alpaca-ops.sh > /tmp/alpaca-ops.sh
#   read -r -a ARGS <<< "$*"; bash /tmp/alpaca-ops.sh "${ARGS[@]}"
#
# So a pushed change takes effect on the next run; the deployed app only
# changes with "deploy". Every command also writes its output to
# /mnt/user/Workfiles/AlpacaBroker/ops/<command>.log, which Claude Code reads
# via \\HOMENAS\Workfiles (text instead of terminal screenshots).
#
# Scope: only the alpaca-broker container, its appdata folder and its
# Workfiles folder. Never other containers, the array or Unraid settings.
# =============================================================================

set -uo pipefail

APP_DIR=/mnt/user/appdata/alpaca-broker
COMPOSE_FILE=/boot/config/plugins/compose.manager/projects/Alpaca-Broker/docker-compose.yml
CONTAINER=alpaca-broker
API=http://localhost:8090/api/v1
OPS_DIR=/mnt/user/Workfiles/AlpacaBroker/ops
LOCK=/tmp/alpaca-deploy.lock
BG_LOG="$OPS_DIR/bg-job.log"

die() { echo "ERROR: $*" >&2; exit 1; }
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

# Collectors run 22:30–~03:00, the nightly chain from 04:30 (Europe/Berlin).
# A restart in that window kills runs (they are only marked stale on the next
# start). The window is Berlin time whatever timezone the host is set to.
guard_window() {
    local hm
    hm=$(TZ=Europe/Berlin date +%H%M)
    if [ "$hm" -ge 2215 ] || [ "$hm" -lt 0515 ]; then
        die "no deploy/restart/run between 22:15 and 05:15 Berlin time (collectors + nightly chain), now $(TZ=Europe/Berlin date +%H:%M)"
    fi
}

# Daytime runs (weekly Sunday jobs, monthly analysis, manual triggers).
# If the API is down there is nothing to protect – allow the deploy.
guard_no_running_jobs() {
    local summary
    if ! summary=$(curl -fsS -m 20 "$API/dashboard/summary" 2>/dev/null); then
        echo "WARN: API not reachable – skipping running-job check"
        return 0
    fi
    if echo "$summary" | grep -Eq '"is_running": ?true'; then
        die "a job is running right now – try again later (alpaca-ops status)"
    fi
}

# Project scripts running inside the container ("run" and "run-bg" both start
# ".venv/bin/python -u scripts/…"). docker top reads the process list from the
# host, so the image needs no procps.
running_scripts() {
    docker top "$CONTAINER" -eo pid,args 2>/dev/null | grep -E 'python -u scripts/' || true
}

# A deploy/restart kills a running script (a feature rebuild would stop halfway).
guard_no_running_scripts() {
    local procs
    procs=$(running_scripts)
    if [ -n "$procs" ]; then
        echo "$procs"
        die "a project script is running inside the container – wait for it (alpaca-ops run-log) or stop it (alpaca-ops run-stop)"
    fi
}

validate_script_args() {
    [[ "${1:-}" =~ ^scripts/[A-Za-z0-9_/]+\.py$ ]] || die "script must match scripts/…/*.py"
    local a
    for a in "${@:2}"; do
        [[ "$a" =~ ^[A-Za-z0-9_.,:=/@+-]+$ ]] || die "invalid argument: $a"
    done
}

deploy_worker() {
    exec 9>"$LOCK"
    flock -n 9 || die "another deploy is running"
    echo "==> $(date -Is) deploy started"
    git -C "$APP_DIR" pull --ff-only || die "git pull failed"
    local commit
    commit=$(git -C "$APP_DIR" log -1 --format='%h %s')
    echo "==> commit: $commit"
    # Build first, then recreate: the old container keeps running during the
    # ~7 min build. Migrations run in the entrypoint of the new container.
    compose build || die "build failed – old container still running"
    # The guards were checked when "deploy" was started, but the build takes
    # ~7 min: the window may have opened or a job may have started meanwhile.
    # Re-check right before the recreate (in a subshell: die exits only that).
    if ! (guard_window && guard_no_running_jobs && guard_no_running_scripts); then
        die "image built, recreate skipped – run deploy again later (old container keeps running)"
    fi
    compose up -d || die "compose up failed"
    echo "$commit (deployed $(date -Is))" > "$OPS_DIR/deployed.txt"
    echo "==> $(date -Is) deploy finished – check: alpaca-ops status"
}

# Detached deploy worker (started by "deploy" below from a private copy, so
# a concurrent run of the wrapper cannot rewrite the file while bash reads it).
# Internal only: it skips the guards of "deploy", so it must not be reachable
# through the User Script argument – "deploy" sets the private env var.
if [ "${1:-}" = "_deploy_worker" ]; then
    [ "${ALPACA_OPS_DEPLOY_WORKER:-}" = "1" ] || die "_deploy_worker is internal – use: deploy"
    unset ALPACA_OPS_DEPLOY_WORKER
    deploy_worker
    exit $?
fi

mkdir -p "$OPS_DIR"
CMD="${1:-help}"
shift || true
ARGS=("$@")

# Mirror all output of this run into ops/<command>.log (latest run only).
LOG="$OPS_DIR/${CMD//[^a-z-]/}.log"
exec > >(tee "$LOG") 2>&1
echo "## alpaca-ops $CMD ${ARGS[*]:-} – $(date -Is)"

case "$CMD" in
    help)
        cat <<'EOF'
alpaca-ops – commands (argument of the "alpaca-ops" user script):
  status                     container, health, API system health, deployed/fetched commit, disk, alpaca cron entries
  logs [N] [SINCE]           last N container log lines (default 500, max 5000), optional SINCE (e.g. 2026-10-16T04:00 or 12h)
  deploy                     git pull + build + recreate (detached; blocked 22:15–05:15 Berlin time and while a job runs; guards re-checked before the recreate)
  deploy-log [N]             progress/result of the last deploy
  restart                    restart the container (same guards as deploy)
  run <scripts/…py> [args]   run a project script inside the container (as app user 99:100; blocked 22:15–05:15 Berlin time); ends when the popup is closed
  run-bg <scripts/…py> [args] same, detached (survives closing the popup; for long runs such as features_rebuild.py); one at a time; output: ops/bg-job.log
  run-log [N]                state of the scripts running in the container + last N lines of ops/bg-job.log
  run-stop                   send SIGTERM to the project scripts running in the container
Output of every run: /mnt/user/Workfiles/AlpacaBroker/ops/<command>.log
EOF
        ;;
    status)
        # guard_window relies on tzdata: both lines must show the same wall time in Berlin
        echo "## time"
        echo "Berlin: $(TZ=Europe/Berlin date '+%F %H:%M %Z')   host: $(date '+%F %H:%M %Z')"
        echo "## container"
        docker inspect -f '{{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}} started={{.State.StartedAt}}' "$CONTAINER" 2>&1
        echo "## image"
        docker image inspect -f 'created={{.Created}}' "$CONTAINER:latest" 2>&1
        echo "## commits"
        echo "deployed:     $(cat "$OPS_DIR/deployed.txt" 2>/dev/null || echo 'unknown (no deploy via alpaca-ops yet)')"
        echo "working tree: $(git -C "$APP_DIR" log -1 --format='%h %ad %s' --date=iso)"
        echo "origin/main:  $(git -C "$APP_DIR" log -1 --format='%h %ad %s' --date=iso origin/main)"
        echo "## health"
        curl -sS -m 10 "$API/health"; echo
        echo "## project scripts running in the container"
        procs=$(running_scripts); echo "${procs:-(none)}"
        echo "## disk"
        df -h /mnt/user/appdata /mnt/user/Workfiles 2>/dev/null
        echo "## cron entries / user scripts that touch the alpaca DB (backup check)"
        { crontab -l 2>/dev/null | grep -nE 'broker_data|postgresql18-alpaca|5435'; \
          grep -rlE 'broker_data|postgresql18-alpaca|5435' /etc/cron.d /etc/cron.daily /etc/cron.hourly \
            /boot/config/plugins/user.scripts/scripts 2>/dev/null; } || echo "(none)"
        ;;
    logs)
        n="${ARGS[0]:-500}"
        [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -le 5000 ] || die "logs: N must be a number ≤ 5000"
        since=()
        if [ -n "${ARGS[1]:-}" ]; then
            [[ "${ARGS[1]}" =~ ^[0-9T:.+-]+[smhZ]?$ ]] || die "logs: invalid SINCE"
            since=(--since "${ARGS[1]}")
        fi
        docker logs --timestamps --tail "$n" "${since[@]}" "$CONTAINER" 2>&1
        ;;
    deploy)
        guard_window
        guard_no_running_jobs
        guard_no_running_scripts
        # Before cp and the log redirect below: a second deploy must neither
        # overwrite the script a running worker is reading nor truncate its log.
        flock -n "$LOCK" true || die "another deploy is running"
        cp "$0" /tmp/alpaca-deploy-worker.sh
        ALPACA_OPS_DEPLOY_WORKER=1 setsid nohup bash /tmp/alpaca-deploy-worker.sh _deploy_worker >"$OPS_DIR/deploy-worker.log" 2>&1 </dev/null &
        echo "deploy started in background – progress: alpaca-ops deploy-log (or ops/deploy-worker.log)"
        ;;
    deploy-log)
        n="${ARGS[0]:-60}"
        [[ "$n" =~ ^[0-9]+$ ]] || die "deploy-log: N must be a number"
        if flock -n "$LOCK" true 2>/dev/null; then echo "## state: not running"; else echo "## state: running"; fi
        tail -n "$n" "$OPS_DIR/deploy-worker.log" 2>/dev/null || echo "(no deploy log yet)"
        ;;
    restart)
        guard_window
        guard_no_running_jobs
        guard_no_running_scripts
        docker restart "$CONTAINER"
        ;;
    run)
        validate_script_args "${ARGS[@]}"
        # scripts may write feature_snapshots → deadlock risk during the nightly chain
        guard_window
        docker exec "$CONTAINER" .venv/bin/python -u "${ARGS[@]}"
        ;;
    run-bg)
        validate_script_args "${ARGS[@]}"
        guard_window
        guard_no_running_scripts
        # setsid/nohup: the run survives closing the User Script popup. The
        # window guard only applies at the start – a long run must end before
        # the nightly chain (04:30, Tue–Sat) or be stopped with run-stop.
        setsid nohup bash -c 'docker exec "$1" .venv/bin/python -u "${@:2}"; rc=$?; echo "## $(date -Is) exit code $rc"' \
            _ "$CONTAINER" "${ARGS[@]}" >"$BG_LOG" 2>&1 </dev/null &
        sleep 3
        echo "started in background: ${ARGS[*]}"
        procs=$(running_scripts); echo "${procs:-WARN: no script process visible yet – check: alpaca-ops run-log}"
        echo "output: ops/bg-job.log (alpaca-ops run-log [N]); it is NOT stopped at 22:15 – mind the nightly chain (04:30 Tue–Sat)"
        ;;
    run-log)
        n="${ARGS[0]:-60}"
        [[ "$n" =~ ^[0-9]+$ ]] || die "run-log: N must be a number"
        procs=$(running_scripts)
        if [ -n "$procs" ]; then echo "## state: running"; echo "$procs"; else echo "## state: not running"; fi
        tail -n "$n" "$BG_LOG" 2>/dev/null || echo "(no background run yet)"
        ;;
    run-stop)
        procs=$(running_scripts)
        [ -n "$procs" ] || { echo "no project script running"; exit 0; }
        echo "$procs"
        # PIDs from docker top are host PIDs – signal them from the host.
        echo "$procs" | awk '{print $1}' | xargs -r kill -TERM
        echo "SIGTERM sent – check: alpaca-ops run-log"
        ;;
    *)
        die "unknown command: $CMD (try: help)"
        ;;
esac
