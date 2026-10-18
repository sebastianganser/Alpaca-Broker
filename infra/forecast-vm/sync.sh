#!/usr/bin/env bash
# =============================================================================
# sync.sh - keep the VM's local pack mirror and the Unraid share in step.
# Only needed for PACK_MODE=rsync (cifs mount of the share). With virtiofs the
# VM sees the share directly and this script does nothing.
#
#   pull  share -> LOCAL_PACK_ROOT : complete session folders (00_uebersicht.md
#         exists), newest PULL_LAST_N, never deletes anything on either side
#   push  LOCAL_PACK_ROOT -> share : ONLY forecasts.yaml, trade_ideas.yaml,
#         prognosen.md, research_sources.md, ONLY into session folders that
#         already exist on the share (this script never creates folders there)
#
# forecasts.yaml is pushed with --ignore-existing: a file already on the share
# is never overwritten (it may be sealed; a changed forecasts.yaml would count
# as late). If the share's copy differs from the local one the script warns -
# the share wins.
#
# Safe to run every few minutes (systemd timer / cron). Safe when the mount is
# down: logs an error, exits 2, the next run retries.
#
# Exit codes: 0 ok / nothing to do, 1 lock wait expired, 2 share unavailable,
#             3 rsync error, 64 bad configuration
# Usage: sync.sh [--dry-run]     (SYNC_LOCK_WAIT=<seconds> waits for the lock)
# =============================================================================
set -Eeuo pipefail
umask 0002

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"
load_config
LOG_FILE="$LOG_DIR/sync.log"
rotate_log "$LOG_FILE"

DRY=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=(--dry-run) ;;
        -h | --help)
            sed -n '2,24p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *) die 64 "unknown option: $arg" ;;
    esac
done

# cifs: no permission/owner bits, 2 s timestamp granularity, bounded I/O wait.
RSYNC_OPTS=(-rt --no-perms --no-owner --no-group --omit-dir-times --modify-window=2
    --timeout=60 "--out-format=  %n" "${DRY[@]}")

if [ "$PACK_MODE" != "rsync" ]; then
    log INFO "PACK_MODE=$PACK_MODE: nothing to sync"
    exit 0
fi

wait_s="${SYNC_LOCK_WAIT:-0}"
if ! acquire_lock sync "$wait_s"; then
    if [ "$wait_s" -gt 0 ]; then
        die 1 "another sync still holds the lock after ${wait_s}s"
    fi
    exit 0 # a previous run is still busy; the timer comes back in 5 minutes
fi

# The share must answer within 20 s. A directory test also triggers a lazy
# (x-systemd.automount) mount and avoids hanging on a dead cifs connection.
if ! timeout 20 test -d "$SHARE_PACK_DIR"; then
    die 2 "share not available: $SHARE_PACK_DIR (mount down? see README, step 'Mount')"
fi
mkdir -p "$LOCAL_PACK_ROOT"

rc=0

pull() {
    local name src dst
    while IFS= read -r name; do
        src="$SHARE_PACK_DIR/$name"
        dst="$LOCAL_PACK_ROOT/$name"
        if [ ! -f "$src/00_uebersicht.md" ]; then
            continue # generator not finished (overview is written last)
        fi
        mkdir -p "$dst"
        # --update: never replace a local file that is newer than the share's
        if ! rsync "${RSYNC_OPTS[@]}" --update "$src/" "$dst/" >>"$LOG_FILE" 2>&1; then
            log ERROR "pull failed for $name"
            rc=3
        fi
    done < <(recent_session_dirs "$SHARE_PACK_DIR" "$PULL_LAST_N")
}

push_file() { # push_file SRC_FILE DST_DIR [extra rsync options...]
    local file="$1" dst="$2"
    shift 2
    if ! rsync "${RSYNC_OPTS[@]}" "$@" "$file" "$dst/" >>"$LOG_FILE" 2>&1; then
        log ERROR "push failed: $file -> $dst"
        rc=3
        return 1
    fi
}

push() {
    local name src dst f
    while IFS= read -r name; do
        src="$LOCAL_PACK_ROOT/$name"
        dst="$SHARE_PACK_DIR/$name"
        if [ ! -d "$dst" ]; then
            log WARN "no folder on the share for $name - not creating it"
            continue
        fi
        # 1. the time-critical file first; never overwrite an existing one
        if [ -f "$src/forecasts.yaml" ]; then
            if [ -e "$dst/forecasts.yaml" ]; then
                if ! cmp -s "$src/forecasts.yaml" "$dst/forecasts.yaml"; then
                    log WARN "$name: forecasts.yaml on the share differs from the local one - share wins, local NOT pushed"
                fi
            elif push_file "$src/forecasts.yaml" "$dst" --ignore-existing; then
                if [ ${#DRY[@]} -eq 0 ] && ! cmp -s "$src/forecasts.yaml" "$dst/forecasts.yaml"; then
                    log ERROR "$name: forecasts.yaml on the share differs after the push"
                    rc=3
                else
                    log INFO "$name: forecasts.yaml pushed"
                fi
            fi
        fi
        # 2. companion files: update when the local copy is newer
        for f in trade_ideas.yaml prognosen.md research_sources.md; do
            if [ -f "$src/$f" ]; then
                push_file "$src/$f" "$dst" --update || true
            fi
        done
    done < <(recent_session_dirs "$LOCAL_PACK_ROOT" "$PUSH_LAST_N")
}

pull
push

if [ "$rc" -eq 0 ]; then
    log INFO "sync ok"
fi
exit "$rc"
