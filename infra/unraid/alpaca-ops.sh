#!/bin/bash
# =============================================================================
# Alpaca-Broker – restricted SSH gateway for Claude Code (Unraid host)
# =============================================================================
# Installed as a *forced command* for exactly one SSH key in root's
# authorized_keys (Unraid WebGUI → Users → root → SSH authorized keys):
#
#   command="bash /boot/config/alpaca-ops.sh",restrict ssh-ed25519 AAAA… claude-code@alpaca-broker
#
# Every SSH call with that key runs this script instead of the requested
# command. The requested command arrives in $SSH_ORIGINAL_COMMAND and only
# the subcommands below are accepted. Other containers, the GynOrg database,
# the array and Unraid settings are not reachable with this key.
#
# The live copy lives OUTSIDE the repo on purpose (a git pull must never
# widen these permissions). Changes take effect only after Sebastian copies
# the file again:
#   cp /mnt/user/appdata/alpaca-broker/infra/unraid/alpaca-ops.sh /boot/config/alpaca-ops.sh
#
# Usage from Windows (see ~/.ssh/config, host alias "alpaca-unraid"):
#   ssh alpaca-unraid help
# =============================================================================

set -uo pipefail

APP_DIR=/mnt/user/appdata/alpaca-broker
COMPOSE_FILE=/boot/config/plugins/compose.manager/projects/Alpaca-Broker/docker-compose.yml
CONTAINER=alpaca-broker
API=http://localhost:8090/api/v1
SELF=/boot/config/alpaca-ops.sh
DEPLOY_LOG=/tmp/alpaca-deploy.log
LOCK=/tmp/alpaca-deploy.lock

die() { echo "ERROR: $*" >&2; exit 1; }
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

# Collectors run 22:30–~03:00, the nightly chain from 04:30. A restart in
# that window kills runs (they are only marked stale on the next start).
guard_window() {
    local hm
    hm=$(date +%H%M)
    if [ "$hm" -ge 2215 ] || [ "$hm" -lt 0515 ]; then
        die "no deploy/restart between 22:15 and 05:15 (collectors + nightly chain), now $(date +%H:%M)"
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
        die "a job is running right now – try again later (ssh alpaca-unraid status)"
    fi
}

deploy_worker() {
    exec 9>"$LOCK"
    flock -n 9 || die "another deploy is running"
    echo "==> $(date -Is) deploy started"
    git -C "$APP_DIR" pull --ff-only || die "git pull failed"
    echo "==> commit: $(git -C "$APP_DIR" log -1 --format='%h %s')"
    # Build first, then recreate: the old container keeps running during the
    # ~7 min build. Migrations run in the entrypoint of the new container.
    compose build || die "build failed – old container still running"
    compose up -d || die "compose up failed"
    echo "==> $(date -Is) deploy finished – check: ssh alpaca-unraid status"
}

# Internal re-invocation for the detached deploy (positional arg, never
# reachable via SSH because the forced command passes no arguments).
if [ "${1:-}" = "_deploy_worker" ]; then
    deploy_worker
    exit $?
fi

read -r -a ARGS <<< "${SSH_ORIGINAL_COMMAND:-help}"
CMD="${ARGS[0]:-help}"

case "$CMD" in
    help)
        cat <<'EOF'
alpaca-ops – allowed commands:
  status                  container state, health, deployed commit, disk
  logs [N] [SINCE]        last N container log lines (max 5000), optional --since (e.g. 2026-10-16T04:00)
  deploy                  git pull + build + recreate (detached; blocked 22:15–05:15 and while a job runs)
  deploy-log [N]          progress/result of the last deploy
  restart                 restart the container (same guards as deploy)
  run <scripts/…py> [args]  run a project script inside the container (as app user 99:100)
  cron                    read-only: cron entries/user scripts that touch the alpaca DB (backup check)
EOF
        ;;
    status)
        echo "## container"
        docker inspect -f '{{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}} started={{.State.StartedAt}}' "$CONTAINER" 2>&1
        echo "## image"
        docker image inspect -f 'created={{.Created}}' "$CONTAINER:latest" 2>&1
        echo "## repo"
        git -C "$APP_DIR" log -1 --format='%h %ad %s' --date=iso
        echo "## health"
        curl -sS -m 10 "$API/health"; echo
        echo "## disk"
        df -h /mnt/user/appdata /mnt/user/Workfiles 2>/dev/null
        ;;
    logs)
        n="${ARGS[1]:-200}"
        [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -le 5000 ] || die "logs: N must be a number ≤ 5000"
        since=()
        if [ -n "${ARGS[2]:-}" ]; then
            [[ "${ARGS[2]}" =~ ^[0-9T:.+-]+[smhZ]?$ ]] || die "logs: invalid SINCE"
            since=(--since "${ARGS[2]}")
        fi
        docker logs --timestamps --tail "$n" "${since[@]}" "$CONTAINER" 2>&1
        ;;
    deploy)
        guard_window
        guard_no_running_jobs
        setsid nohup bash "$SELF" _deploy_worker >"$DEPLOY_LOG" 2>&1 </dev/null &
        echo "deploy started in background – follow with: ssh alpaca-unraid deploy-log"
        ;;
    deploy-log)
        n="${ARGS[1]:-60}"
        [[ "$n" =~ ^[0-9]+$ ]] || die "deploy-log: N must be a number"
        if flock -n "$LOCK" true 2>/dev/null; then echo "## state: finished"; else echo "## state: running"; fi
        tail -n "$n" "$DEPLOY_LOG" 2>/dev/null || echo "(no deploy log yet)"
        ;;
    restart)
        guard_window
        guard_no_running_jobs
        docker restart "$CONTAINER"
        ;;
    run)
        script="${ARGS[1]:-}"
        [[ "$script" =~ ^scripts/[A-Za-z0-9_/]+\.py$ ]] || die "run: script must match scripts/…/*.py"
        for a in "${ARGS[@]:2}"; do
            [[ "$a" =~ ^[A-Za-z0-9_.,:=/@+-]+$ ]] || die "run: invalid argument: $a"
        done
        exec docker exec "$CONTAINER" .venv/bin/python "$script" "${ARGS[@]:2}"
        ;;
    cron)
        echo "## root crontab (lines mentioning the alpaca DB)"
        crontab -l 2>/dev/null | grep -nE 'broker_data|alpaca|5435' || echo "(none)"
        echo "## cron files / user scripts mentioning the alpaca DB"
        grep -rlE 'broker_data|postgresql18-alpaca|5435' /etc/cron.d /etc/cron.daily /etc/cron.hourly \
            /boot/config/plugins/user.scripts/scripts 2>/dev/null || echo "(none)"
        ;;
    *)
        die "command not allowed: $CMD (try: help)"
        ;;
esac
