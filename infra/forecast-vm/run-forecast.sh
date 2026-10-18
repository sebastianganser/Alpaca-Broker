#!/usr/bin/env bash
# =============================================================================
# run-forecast.sh - one headless forecast run with Claude Code (skill
# signal-warehouse-prognose) on the newest complete context pack.
#
# Meant for a timer/cron at ~07:30 Europe/Berlin Tue-Sat (plus retries at 09:30
# and 11:30 - they do nothing once forecasts.yaml exists). Idempotent:
#
#   1. config + lock (one run at a time), claude login check
#   2. rsync mode: pull the share first (sync.sh)
#   3. newest pack with 00_uebersicht.md; stop if pack_format != 2, if
#      forecasts.yaml already exists, if the attempt limit is reached or if the
#      last seal (09:25 New York) is too close (deadline is read from the overview)
#   4. forecaster_version = <swp-vN from SKILL.md>+<MODEL>+pack<format>, handed
#      to the skill as a binding parameter (so model id and file always agree)
#   5. claude -p "/signal-warehouse-prognose ..." with a pinned model, permission
#      mode dontAsk and a minimal allow list (see README "Berechtigungen")
#   6. check forecasts.yaml (schema, session, decided_at offset + before the
#      open, forecaster_version, no forbidden fields) - while there is still
#      time to fix it by hand before the seal
#   7. rsync mode: push the result to the share (sync.sh) and verify it
#
# Exit codes: 0 done / nothing to do, 10 not logged in, 20 claude failed,
#   21 claude timed out, 30 output check failed, 31 push to the share failed,
#   40 too late for this pack, 41 attempt limit reached, 42 unexpected pack
#   format, 43 skill pin mismatch, 64 bad usage/config, 70 missing prerequisite
# Usage: run-forecast.sh [--dry-run] [--ignore-deadline]
#   --dry-run          do everything except calling claude; prints the command line
#   --ignore-deadline  test runs on an OLD pack copy; only with PACK_MODE=local
# =============================================================================
set -Eeuo pipefail
umask 0002 # files stay group-writable (virtiofs/cifs: the Unraid container must read them)

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"
load_config
LOG_FILE="$LOG_DIR/run.log"
rotate_log "$LOG_FILE" 5242880

DRY_RUN=0
IGNORE_DEADLINE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --ignore-deadline) IGNORE_DEADLINE=1 ;;
        -h | --help)
            sed -n '2,30p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *) die 64 "unknown option: $arg" ;;
    esac
done
if [ "$IGNORE_DEADLINE" -eq 1 ] && [ "$PACK_MODE" != "local" ]; then
    die 64 "--ignore-deadline is only allowed with PACK_MODE=local (test copy)"
fi

# Sites whose terms forbid automated access (Sebastian, 2026-10-18). Enforced
# here AND in claude-settings.example.json; the skill says the same in words.
BLOCKED_DOMAINS=(tradingview.com stockcharts.com barchart.com investing.com yahoo.com
    seekingalpha.com marketbeat.com zacks.com fool.com)

# Same list as docs/FORECASTS.md section 3 (the server rejects these rows).
FORBIDDEN_FORECAST_KEYS='limit|limit_price|entry|exit|target|target_pct|target_price|price_target|stop|stop_pct|stop_price|stop_loss|take_profit|trailing|size|position|position_size|quantity|qty|shares|weight|leverage|order|action|buy|sell'
FORBIDDEN_IDEA_KEYS='size|position|position_size|quantity|qty|shares|weight|leverage|order|action|buy|sell|target|target_pct|target_price|price_target|take_profit|stop|stop_pct|stop_price|stop_loss|trailing|limit|limit_price'

key_value() { # key_value FILE KEY - first top-level value, without comment/quotes
    sed -n "s/^$2:[[:space:]]*\([^ #]*\).*/\1/p" "$1" | head -n1 | tr -d "\"'"
}

check_forecasts() { # returns the number of problems
    local f="$PACK_DIR/forecasts.yaml" problems=0 v decided rows
    if [ ! -s "$f" ]; then
        log ERROR "forecasts.yaml missing or empty: $f"
        return 1
    fi
    grep -qE '^schema:[[:space:]]*forecasts/v1[[:space:]]*(#.*)?$' "$f" || { log ERROR "check: schema is not forecasts/v1"; problems=$((problems + 1)); }
    [ "$(key_value "$f" session)" = "$PACK_DATE" ] || { log ERROR "check: session is not $PACK_DATE"; problems=$((problems + 1)); }
    v=$(key_value "$f" forecaster_version)
    [ "$v" = "$FORECASTER_VERSION" ] || { log ERROR "check: forecaster_version '$v' != expected '$FORECASTER_VERSION' (a mismatch restarts the pre-registered sample!)"; problems=$((problems + 1)); }
    decided=$(key_value "$f" decided_at)
    # A decided_at after the file was written is a guess, not a measurement
    # (seen in the first test run): replace it by the file's write time.
    if printf '%s' "$decided" | grep -qE '(Z|[+-][0-9]{2}:[0-9]{2})$'; then
        local d_epoch m_epoch fixed
        d_epoch=$(date -d "$decided" +%s 2>/dev/null || echo 0)
        m_epoch=$(stat -c %Y "$f")
        if [ "$d_epoch" -gt $((m_epoch + 120)) ]; then
            fixed=$(date -d "@$m_epoch" --iso-8601=seconds)
            sed -i -E "s|^decided_at:.*$|decided_at: $fixed|" "$f"
            log WARN "check: decided_at $decided lies after the file was written - corrected to $fixed"
            decided="$fixed"
        fi
    fi
    if printf '%s' "$decided" | grep -qE '(Z|[+-][0-9]{2}:[0-9]{2})$'; then
        if [ -n "${OPEN_EPOCH:-}" ] && [ "$IGNORE_DEADLINE" -eq 0 ]; then
            if [ "$(date -d "$decided" +%s 2>/dev/null || echo 9999999999)" -ge "$OPEN_EPOCH" ]; then
                log ERROR "check: decided_at $decided is not before the open"
                problems=$((problems + 1))
            fi
        fi
    else
        log ERROR "check: decided_at '$decided' has no time zone offset (file would be rejected)"
        problems=$((problems + 1))
    fi
    if grep -nE "^[[:space:]]*(-[[:space:]]+)?($FORBIDDEN_FORECAST_KEYS):" "$f" >>"$LOG_FILE"; then
        log ERROR "check: forbidden trade fields in forecasts.yaml (lines in $LOG_FILE)"
        problems=$((problems + 1))
    fi
    rows=$(grep -cE '^[[:space:]]*-[[:space:]]+ticker:' "$f" || true)
    log INFO "check: forecasts.yaml has $rows rows, version $v, decided_at $decided"
    return "$problems"
}

check_trade_ideas() { # warnings only: the file is not evaluated
    local f="$PACK_DIR/trade_ideas.yaml"
    if [ ! -s "$f" ]; then
        log WARN "trade_ideas.yaml missing (not fatal)"
        return 0
    fi
    grep -qE '^schema:[[:space:]]*trade_ideas/v1[[:space:]]*(#.*)?$' "$f" || log WARN "check: trade_ideas.yaml schema is not trade_ideas/v1"
    [ "$(key_value "$f" forecaster_version)" = "$FORECASTER_VERSION" ] || log WARN "check: trade_ideas.yaml forecaster_version differs"
    if grep -nE "^[[:space:]]*(-[[:space:]]+)?($FORBIDDEN_IDEA_KEYS):" "$f" >>"$LOG_FILE"; then
        log WARN "check: trade_ideas.yaml contains size/order/target fields (lines in $LOG_FILE)"
    fi
}

# --------------------------------------------------------------------------- #

acquire_lock run 0 || {
    log INFO "another run is active - exiting"
    exit 0
}

log INFO "=== run-forecast start: mode=$PACK_MODE model=$MODEL dry_run=$DRY_RUN ==="

command -v "$CLAUDE_BIN" >/dev/null 2>&1 || die 70 "claude not found (install: see README step 5)"
command -v timeout >/dev/null 2>&1 || die 70 "timeout (coreutils) not found"

if [ -n "$OAUTH_TOKEN_FILE" ]; then
    [ -r "$OAUTH_TOKEN_FILE" ] || die 70 "OAUTH_TOKEN_FILE not readable: $OAUTH_TOKEN_FILE"
    if [ "$(stat -c %a "$OAUTH_TOKEN_FILE")" != "600" ]; then
        die 70 "$OAUTH_TOKEN_FILE must have mode 600"
    fi
    CLAUDE_CODE_OAUTH_TOKEN=$(<"$OAUTH_TOKEN_FILE")
    export CLAUDE_CODE_OAUTH_TOKEN
fi
log INFO "claude $("$CLAUDE_BIN" --version 2>&1 | head -n1)"
if ! "$CLAUDE_BIN" auth status >/dev/null 2>&1; then
    die 10 "claude is not logged in (run 'claude' interactively once, or use OAUTH_TOKEN_FILE)"
fi

# 2. fresh packs
if [ "$PACK_MODE" = "rsync" ]; then
    if ! SYNC_LOCK_WAIT=120 bash "$SCRIPT_DIR/sync.sh"; then
        log WARN "pull failed - continuing with the local mirror"
    fi
fi

# 3. which pack
if ! PACK_DIR=$(latest_complete_pack); then
    log WARN "no complete pack under $PACK_ROOT yet (nightly chain not done? a retry timer will look again)"
    exit 0
fi
PACK_DATE=$(basename "$PACK_DIR")
log INFO "newest complete pack: $PACK_DATE"

first_ticker_file=$(find "$PACK_DIR" -maxdepth 1 -name '[A-Z]*.md' ! -name '00_*' ! -name 'prognosen.md' -print -quit)
PACK_FORMAT=""
if [ -n "$first_ticker_file" ]; then
    PACK_FORMAT=$(sed -n 's/^pack_format:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$first_ticker_file" | head -n1)
fi
if [ "$PACK_FORMAT" != "2" ]; then
    die 42 "pack $PACK_DATE: pack_format '${PACK_FORMAT:-none}' (expected 2) - not forecasting an old/unknown format"
fi

if [ -e "$PACK_DIR/forecasts.yaml" ]; then
    log INFO "forecasts.yaml already exists for $PACK_DATE - nothing to do"
    exit 0
fi

# deadline: open = 09:30 NY of the next trading day, last seal 5 minutes before
OPEN_EPOCH=""
if OPEN_EPOCH=$(deadline_epoch_from_overview "$PACK_DIR/00_uebersicht.md"); then
    seal_epoch=$((OPEN_EPOCH - 300))
    now_epoch=$(date +%s)
    minutes_left=$(((seal_epoch - now_epoch) / 60))
    log INFO "last seal in ${minutes_left} min ($(TZ=America/New_York date -d "@$seal_epoch" '+%F %H:%M %Z'))"
    if [ "$IGNORE_DEADLINE" -eq 0 ] && [ "$minutes_left" -lt "$MIN_MINUTES_BEFORE_SEAL" ]; then
        die 40 "pack $PACK_DATE: only ${minutes_left} min until the last seal (< $MIN_MINUTES_BEFORE_SEAL) - not starting; the day is missed"
    fi
else
    OPEN_EPOCH=""
    log WARN "could not read the deadline from 00_uebersicht.md (wording changed?) - the skill checks it itself"
fi

# 4. forecaster_version from the skill + the pinned model
SKILL_FILE="$REPO_DIR/.claude/skills/signal-warehouse-prognose/SKILL.md"
[ -r "$SKILL_FILE" ] || die 70 "skill not found: $SKILL_FILE (clone the repo, README step 6)"
SKILL_VERSION=$(grep -m1 -oE 'Version \*\*swp-v[0-9]+\*\*' "$SKILL_FILE" | grep -oE 'swp-v[0-9]+' || true)
[ -n "$SKILL_VERSION" ] || die 70 "no 'Version **swp-vN**' marker in $SKILL_FILE"
if [ -n "$EXPECT_SKILL_VERSION" ] && [ "$SKILL_VERSION" != "$EXPECT_SKILL_VERSION" ]; then
    die 43 "skill version $SKILL_VERSION != pinned EXPECT_SKILL_VERSION=$EXPECT_SKILL_VERSION (git pull changed the skill? changing it restarts the sample)"
fi
SKILL_COMMIT=$(git -C "$REPO_DIR" log -1 --format=%H -- .claude/skills/signal-warehouse-prognose 2>/dev/null || echo unknown)
if [ -n "$EXPECT_SKILL_COMMIT" ] && [ "$SKILL_COMMIT" != "$EXPECT_SKILL_COMMIT" ]; then
    die 43 "skill commit $SKILL_COMMIT != pinned EXPECT_SKILL_COMMIT=$EXPECT_SKILL_COMMIT"
fi
if [ -n "$(git -C "$REPO_DIR" status --porcelain -- .claude/skills/signal-warehouse-prognose 2>/dev/null)" ]; then
    log WARN "skill directory has local modifications (uncommitted) - the run is not reproducible from git"
fi
FORECASTER_VERSION="${SKILL_VERSION}+${MODEL}+pack${PACK_FORMAT}"
if [ "${#FORECASTER_VERSION}" -gt 64 ]; then
    die 64 "forecaster_version '$FORECASTER_VERSION' is longer than 64 characters (shorten MODEL?)"
fi
log INFO "forecaster_version=$FORECASTER_VERSION skill_commit=$SKILL_COMMIT"

# 5. attempt limit (guards the budget against retry loops)
attempts_file="$STATE_DIR/attempts-$PACK_DATE"
attempts=$(cat "$attempts_file" 2>/dev/null || echo 0)
if [ "$DRY_RUN" -eq 0 ] && [ "$attempts" -ge "$MAX_ATTEMPTS" ]; then
    die 41 "pack $PACK_DATE: $attempts attempts already used (MAX_ATTEMPTS=$MAX_ATTEMPTS) without a forecasts.yaml - look at $LOG_DIR"
fi

# 6. the command
ENTRY_TARGETS=("$PACK_DIR/forecasts.yaml" "$PACK_DIR/trade_ideas.yaml"
    "$PACK_DIR/prognosen.md" "$PACK_DIR/research_sources.md")
ALLOWED=("Read(//${PACK_ROOT#/}/**)" "Read(//${REPO_DIR#/}/**)" "Glob" "Grep" "WebSearch" "WebFetch"
    "Skill" "Bash(date)" "Bash(date *)")
for t in "${ENTRY_TARGETS[@]}"; do
    ALLOWED+=("Edit(//${t#/})") # Edit rules cover the Write tool as well
done
DENIED=("Read(~/.claude/**)" "Read(~/.claude.json)" "Read(~/.ssh/**)" "Read(~/.config/forecast-vm/**)"
    "Bash(curl *)" "Bash(wget *)" "Bash(ssh *)" "Bash(scp *)" "Bash(sudo *)")
for dom in "${BLOCKED_DOMAINS[@]}"; do
    DENIED+=("WebFetch(domain:$dom)" "WebFetch(domain:*.$dom)")
done

RUN_MODE="headless"
if [ "$IGNORE_DEADLINE" -eq 1 ]; then
    RUN_MODE="test"
fi
PROMPT="/signal-warehouse-prognose pack=${PACK_DIR} forecaster_version=${FORECASTER_VERSION} mode=${RUN_MODE}"

mkdir -p "$WORK_DIR"
stamp=$(date +%Y%m%dT%H%M%S)
RUN_JSON="$LOG_DIR/run-${PACK_DATE}-${stamp}.json"
RUN_ERR="$LOG_DIR/run-${PACK_DATE}-${stamp}.stderr"

CMD=(timeout --signal=INT --kill-after=60 "${RUN_TIMEOUT_MIN}m"
    "$CLAUDE_BIN" -p "$PROMPT"
    --model "$MODEL"
    --permission-mode dontAsk
    --tools "Read,Write,Edit,Glob,Grep,Bash,WebFetch,WebSearch,Skill"
    --allowedTools "${ALLOWED[@]}"
    --disallowedTools "${DENIED[@]}"
    --add-dir "$REPO_DIR" --add-dir "$PACK_ROOT"
    --max-budget-usd "$MAX_BUDGET_USD" --max-turns "$MAX_TURNS"
    --output-format json)
# printf %q: copy-pasteable into a shell; contains no secret (the OAuth token is in the environment)
log INFO "cwd=$WORK_DIR command: $(printf '%q ' "${CMD[@]}")"

if [ "$DRY_RUN" -eq 1 ]; then
    log INFO "dry run: claude not called. pack=$PACK_DIR open_epoch=${OPEN_EPOCH:-unknown}"
    exit 0
fi

echo $((attempts + 1)) >"$attempts_file"
log INFO "starting claude (attempt $((attempts + 1))/$MAX_ATTEMPTS), json -> $RUN_JSON"
set +e
(cd "$WORK_DIR" && "${CMD[@]}" >"$RUN_JSON" 2>"$RUN_ERR")
claude_rc=$?
set -e

if command -v jq >/dev/null 2>&1 && [ -s "$RUN_JSON" ]; then
    log INFO "claude result: $(jq -r '"is_error=\(.is_error // "?") subtype=\(.subtype // "?") turns=\(.num_turns // "?") cost_usd=\(.total_cost_usd // "?") duration_s=\((.duration_ms // 0) / 1000 | floor)"' "$RUN_JSON" 2>/dev/null || echo 'result json not parseable')"
fi
case "$claude_rc" in
    0) ;;
    124 | 137) die 21 "claude timed out after ${RUN_TIMEOUT_MIN} min (rc=$claude_rc); see $RUN_JSON" ;;
    *) log ERROR "claude exited with rc=$claude_rc; see $RUN_ERR" ;;
esac

# 7. check the output
if [ ! -e "$PACK_DIR/forecasts.yaml" ]; then
    die 20 "no forecasts.yaml after the run (rc=$claude_rc); see $RUN_JSON / $RUN_ERR - a retry timer may try again ($((attempts + 1))/$MAX_ATTEMPTS used)"
fi
problems=0
check_forecasts || problems=$?
check_trade_ideas
if [ "$problems" -gt 0 ]; then
    log ERROR "forecasts.yaml has $problems problem(s). If the seal (09:25 New York) has not run yet, fix the file BY HAND now; afterwards it counts as late."
fi

# 8. hand it to the share
push_rc=0
if [ "$PACK_MODE" = "rsync" ]; then
    for try in 1 2 3; do
        if SYNC_LOCK_WAIT=120 bash "$SCRIPT_DIR/sync.sh"; then
            push_rc=0
            break
        fi
        push_rc=1
        log WARN "push attempt $try failed - retrying in 30 s"
        sleep 30
    done
    if [ "$push_rc" -eq 0 ] && ! cmp -s "$PACK_DIR/forecasts.yaml" "$SHARE_PACK_DIR/$PACK_DATE/forecasts.yaml"; then
        push_rc=1
    fi
    if [ "$push_rc" -ne 0 ]; then
        log ERROR "forecasts.yaml is NOT on the share. The 5-minute sync timer keeps trying; check the mount before 09:25 New York."
    fi
fi

printf '%s pack=%s version=%s problems=%s push_rc=%s\n' "$(date --iso-8601=seconds)" "$PACK_DATE" "$FORECASTER_VERSION" "$problems" "$push_rc" >"$STATE_DIR/last-run"
if [ "$problems" -gt 0 ]; then
    exit 30
fi
if [ "$push_rc" -ne 0 ]; then
    exit 31
fi
log INFO "=== done: $PACK_DATE ==="
