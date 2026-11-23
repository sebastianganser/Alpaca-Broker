#!/usr/bin/env bash
# =============================================================================
# forecast-vm – provisioning after the unattended install (autoinstall/)
# =============================================================================
# Run by Claude Code as the maintenance user "claudeadmin" (passwordless sudo):
#   sudo bash provision.sh /path/to/alpaca.bundle
# Idempotent: a second run only updates the repo from the new bundle and leaves
# config files that already exist alone.
#
#  1. system update + packages, qemu guest agent, time zone Europe/Berlin
#  2. virtiofs mount of the Unraid share (tag "alpacabroker" -> /mnt/alpaca)
#  3. run user "forecast": group users, linger (user timers without a login)
#  4. as "forecast": repo from the git bundle (sparse: skill, docs, VM package),
#     Claude Code CLI (native installer), forecast.env, ~/.claude/settings.json,
#     systemd user timer forecast-run.timer
#
# The Claude login is NOT done here: Sebastian runs `claude setup-token` once
# and stores the token alone in ~/.config/forecast-vm/claude.token (mode 600).
# =============================================================================
set -euo pipefail
export LC_ALL=C

BUNDLE="${1:?usage: sudo bash provision.sh /path/to/alpaca.bundle}"
RUN_USER="${RUN_USER:-forecast}"
MOUNT_TAG="${MOUNT_TAG:-alpacabroker}"
MOUNT_DIR="${MOUNT_DIR:-/mnt/alpaca}"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
[ -r "$BUNDLE" ] || { echo "bundle not readable: $BUNDLE" >&2; exit 1; }
id "$RUN_USER" >/dev/null 2>&1 || { echo "user $RUN_USER missing" >&2; exit 1; }
step() { echo; echo "==> $*"; }

step "1/4 system update, packages, time zone"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get -y -q upgrade
apt-get -y -q install git rsync jq curl ca-certificates util-linux qemu-guest-agent
systemctl enable --now qemu-guest-agent >/dev/null 2>&1 || true
timedatectl set-timezone Europe/Berlin
timedatectl | grep -iE 'time zone|synchronized'

step "2/4 virtiofs mount $MOUNT_TAG -> $MOUNT_DIR"
mkdir -p "$MOUNT_DIR"
grep -qE "^${MOUNT_TAG}[[:space:]]+${MOUNT_DIR}[[:space:]]" /etc/fstab \
    || echo "${MOUNT_TAG} ${MOUNT_DIR} virtiofs defaults,nofail 0 0" >> /etc/fstab
systemctl daemon-reload
mountpoint -q "$MOUNT_DIR" || mount "$MOUNT_DIR"
ls -ld "$MOUNT_DIR/context_packs"

step "3/4 run user $RUN_USER: group users, linger"
usermod -aG users "$RUN_USER"
loginctl enable-linger "$RUN_USER"

step "4/4 repo, Claude Code, config and timer as $RUN_USER"
# stage the bundle in a fresh directory the run user can read (the argument
# may itself live in /tmp, so never copy it onto a fixed name)
STAGE=$(mktemp -d)
chmod 755 "$STAGE"
install -m 644 "$BUNDLE" "$STAGE/alpaca.bundle"
sudo -u "$RUN_USER" -H MOUNT_DIR="$MOUNT_DIR" BUNDLE_FILE="$STAGE/alpaca.bundle" bash -s <<'EOS'
set -euo pipefail
cd "$HOME"
mkdir -p alpaca/workdir alpaca/state alpaca/logs .config/forecast-vm .config/systemd/user .claude

if [ -d alpaca/repo/.git ]; then
    git -C alpaca/repo pull -q --ff-only "$BUNDLE_FILE" main
else
    # the bundle carries refs/heads/main but no HEAD: name the branch
    git clone -q --branch main "$BUNDLE_FILE" alpaca/repo
    git -C alpaca/repo sparse-checkout set .claude/skills docs infra/forecast-vm
fi
echo "repo at $(git -C alpaca/repo log -1 --format='%h %s')"

if [ ! -x .local/bin/claude ]; then
    curl -fsSL https://claude.ai/install.sh | bash
fi
.local/bin/claude --version

PKG=alpaca/repo/infra/forecast-vm
CFG=.config/forecast-vm/forecast.env
if [ ! -f "$CFG" ]; then
    sed -e 's|^PACK_MODE=.*|PACK_MODE=virtiofs|' \
        -e "s|^SHARE_PACK_DIR=.*|SHARE_PACK_DIR=${MOUNT_DIR}/context_packs|" \
        -e 's|^#OAUTH_TOKEN_FILE=|OAUTH_TOKEN_FILE=|' \
        "$PKG/forecast.env.example" > "$CFG"
    chmod 600 "$CFG"
fi
grep -E '^(PACK_MODE|SHARE_PACK_DIR|MODEL|EXPECT_SKILL_VERSION|OAUTH_TOKEN_FILE)=' "$CFG"

if [ ! -f .claude/settings.json ]; then
    sed "s|~/alpaca/context_packs|/${MOUNT_DIR}/context_packs|g" \
        "$PKG/claude-settings.example.json" > .claude/settings.json
fi

cp "$PKG/systemd/forecast-run.service" "$PKG/systemd/forecast-run.timer" .config/systemd/user/
EOS
rm -rf "$STAGE"

user_systemctl() {
    sudo -u "$RUN_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$RUN_USER")" systemctl --user "$@"
}
user_systemctl daemon-reload
user_systemctl enable --now forecast-run.timer
user_systemctl list-timers --no-pager

echo
echo "done. Missing only: Sebastian's one-time 'claude setup-token' ->"
echo "  /home/${RUN_USER}/.config/forecast-vm/claude.token (mode 600, owner ${RUN_USER})"
