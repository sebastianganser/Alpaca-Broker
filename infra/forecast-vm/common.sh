# shellcheck shell=bash
# =============================================================================
# Alpaca-Broker forecast VM - shared helpers for sync.sh and run-forecast.sh.
# Sourced, never executed. No secrets in here or in the repository.
#
# Configuration comes from ONE env file (default ~/.config/forecast-vm/
# forecast.env, template: forecast.env.example). The file wins over the
# caller's environment; use another file via FORECAST_ENV=... for experiments.
# =============================================================================

FORECAST_ENV="${FORECAST_ENV:-$HOME/.config/forecast-vm/forecast.env}"

# Cron and systemd user units start with a minimal PATH; the native Claude Code
# installer puts the launcher into ~/.local/bin.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

load_config() {
    if [ -r "$FORECAST_ENV" ]; then
        # shellcheck disable=SC1090
        . "$FORECAST_ENV"
    fi

    # Where the packs live: rsync | virtiofs | local (local = test copy, no sync)
    : "${PACK_MODE:=rsync}"
    # Directory with the context_packs of the Unraid share as seen from the VM
    # (virtiofs mount or cifs mount); required for rsync and virtiofs.
    : "${SHARE_PACK_DIR:=/mnt/unraid-workfiles/AlpacaBroker/context_packs}"
    # Local mirror (rsync mode) or test copy (local mode).
    : "${LOCAL_PACK_ROOT:=$HOME/alpaca/context_packs}"

    : "${REPO_DIR:=$HOME/alpaca/repo}"          # clone with .claude/skills and docs
    : "${WORK_DIR:=$HOME/alpaca/workdir}"       # empty working directory (no CLAUDE.md)
    : "${STATE_DIR:=$HOME/alpaca/state}"        # locks, attempt counters (LOCAL disk: flock)
    : "${LOG_DIR:=$HOME/alpaca/logs}"

    : "${CLAUDE_BIN:=claude}"
    # PINNED model, full id (no alias such as "opus": the alias moves). Part of
    # forecaster_version - changing it starts a NEW pre-registered sample.
    : "${MODEL:=claude-opus-5-5}"
    : "${MAX_BUDGET_USD:=12}"                   # runaway guard, tune after the first runs
    : "${MAX_TURNS:=300}"
    : "${RUN_TIMEOUT_MIN:=150}"
    : "${MIN_MINUTES_BEFORE_SEAL:=45}"          # do not start with less time left
    : "${MAX_ATTEMPTS:=2}"                      # claude launches per pack
    : "${EXPECT_SKILL_VERSION:=}"               # optional pin, e.g. swp-v2
    : "${EXPECT_SKILL_COMMIT:=}"                # optional pin: last commit touching the skill
    : "${OAUTH_TOKEN_FILE:=}"                   # optional: file with a `claude setup-token` token (chmod 600)

    : "${PULL_LAST_N:=6}"
    : "${PUSH_LAST_N:=6}"

    case "$PACK_MODE" in
        virtiofs) PACK_ROOT="$SHARE_PACK_DIR" ;;
        rsync | local) PACK_ROOT="$LOCAL_PACK_ROOT" ;;
        *)
            echo "forecast-vm: unknown PACK_MODE '$PACK_MODE' (rsync|virtiofs|local)" >&2
            return 64
            ;;
    esac
    mkdir -p "$STATE_DIR" "$LOG_DIR"
}

# log LEVEL message... - to stderr (journal) and to $LOG_FILE
log() {
    local level="$1"
    shift
    local line
    line="$(date --iso-8601=seconds) [$level] $*"
    printf '%s\n' "$line" >&2
    if [ -n "${LOG_FILE:-}" ]; then
        printf '%s\n' "$line" >>"$LOG_FILE"
    fi
}

# die EXITCODE message...
die() {
    local code="$1"
    shift
    log ERROR "$*"
    exit "$code"
}

# Keep a log file below max_bytes (one generation .1 is enough).
rotate_log() {
    local file="$1" max_bytes="${2:-2097152}" size
    [ -f "$file" ] || return 0
    size=$(stat -c %s "$file")
    if [ "$size" -gt "$max_bytes" ]; then
        mv -f "$file" "$file.1"
    fi
}

# acquire_lock NAME [WAIT_SECONDS] - exclusive lock on LOCAL disk (flock does not
# work reliably on virtiofs/cifs). Holds the lock until the process exits.
acquire_lock() {
    local name="$1" wait="${2:-0}"
    exec {LOCK_FD}>"$STATE_DIR/$name.lock"
    if [ "$wait" -gt 0 ]; then
        flock -w "$wait" "$LOCK_FD"
    else
        flock -n "$LOCK_FD"
    fi
}

# Session folders YYYY-MM-DD below BASE, newest N, oldest first (names only).
recent_session_dirs() {
    local base="$1" n="$2" i start
    local -a dirs=("$base"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])
    start=$((${#dirs[@]} - n))
    if [ "$start" -lt 0 ]; then
        start=0
    fi
    for ((i = start; i < ${#dirs[@]}; i++)); do
        if [ -d "${dirs[i]}" ]; then
            basename "${dirs[i]}"
        fi
    done
}

# Newest session folder under PACK_ROOT that contains 00_uebersicht.md (the
# generator writes it last, so its presence means the pack is complete).
latest_complete_pack() {
    local -a dirs=("$PACK_ROOT"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])
    local i
    for ((i = ${#dirs[@]} - 1; i >= 0; i--)); do
        if [ -f "${dirs[i]}/00_uebersicht.md" ]; then
            printf '%s\n' "${dirs[i]}"
            return 0
        fi
    done
    return 1
}

# Epoch of "YYYY-MM-DD HH:MM" interpreted in New York time.
ny_epoch() {
    TZ=America/New_York date -d "$1" +%s
}

# Deadline (the open, 09:30 New York) of a pack from its overview, as epoch.
# The overview says: "Frist: vor der Eröffnung am 2026-10-19 09:30 New York (...)".
# Returns 1 if the line is not found (wording changed) - callers must cope.
deadline_epoch_from_overview() {
    local overview="$1" stamp
    stamp=$(grep -m1 -E 'Frist.*[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} New York' "$overview" |
        grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} New York' | head -n1 |
        sed 's/ New York$//' || true)
    if [ -z "$stamp" ]; then
        return 1
    fi
    ny_epoch "$stamp"
}
