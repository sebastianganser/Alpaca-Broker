#!/bin/bash
# =============================================================================
# forecast-vm – one-time root setup (Ubuntu Server 26.04, Unraid VM)
# =============================================================================
# Run ONCE by Sebastian:  sudo bash setup-vm-root.sh
# Idempotent: a second run changes nothing. Does everything that needs sudo,
# so the rest of the setup (README sections 3-8) runs as the normal user and
# Claude Code can do it over SSH with its own key – never with a password.
#
#  1. system update + packages (git, rsync, jq, curl, util-linux, ca-certificates)
#  2. grow the root LV to the whole 30 GB vdisk (Ubuntu's installer uses half)
#  3. time zone Europe/Berlin (timers are written in Berlin time)
#  4. virtiofs mount of the Unraid share (mount tag set in the Unraid VM form)
#  5. user in group "users" (gid 100 = owner group of the Unraid share)
#  6. linger for user systemd timers (they run without a login session)
#  7. Claude Code's SSH public key for this user (key auth, no password)
# =============================================================================
set -euo pipefail
export LC_ALL=C   # decimal point for vgs/awk (a German locale prints 13,47)

MOUNT_TAG="${MOUNT_TAG:-alpacabroker}"   # Unraid VM form: "Unraid Mount Tag"
MOUNT_DIR="${MOUNT_DIR:-/mnt/alpaca}"
CLAUDE_PUBKEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAszZgGRpskbaUa4hzfzbRrUTzj8D+HljEyaRuFRs+BO claude-code@alpaca-broker forecast-vm'

[ "$(id -u)" -eq 0 ] || { echo "run with sudo: sudo bash $0" >&2; exit 1; }
USER_NAME="${SUDO_USER:-forecast}"
id "$USER_NAME" >/dev/null 2>&1 || { echo "user $USER_NAME not found" >&2; exit 1; }
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
step() { echo; echo "==> $*"; }

step "1/7 system update and packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get -y -q upgrade
apt-get -y -q install git rsync jq curl util-linux ca-certificates

step "2/7 root filesystem to the full vdisk"
LV=$(findmnt -no SOURCE /)
if [[ "$LV" == /dev/mapper/* ]]; then
    VG_FREE=$(vgs --noheadings --units g -o vg_free "$(lvs --noheadings -o vg_name "$LV" | xargs)" | tr -d ' g')
    if awk "BEGIN{exit !($VG_FREE > 0.5)}"; then
        lvextend -l +100%FREE -r "$LV"
    else
        echo "already uses the whole volume group"
    fi
else
    echo "root is not on LVM ($LV) – nothing to grow"
fi
df -h /

step "3/7 time zone Europe/Berlin"
timedatectl set-timezone Europe/Berlin
timedatectl | grep -i 'time zone'

step "4/7 virtiofs mount $MOUNT_TAG -> $MOUNT_DIR"
mkdir -p "$MOUNT_DIR"
if ! grep -qE "^${MOUNT_TAG}[[:space:]]+${MOUNT_DIR}[[:space:]]" /etc/fstab; then
    cp /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d%H%M%S)"
    echo "${MOUNT_TAG} ${MOUNT_DIR} virtiofs defaults,nofail 0 0" >> /etc/fstab
fi
systemctl daemon-reload
mountpoint -q "$MOUNT_DIR" || mount "$MOUNT_DIR"
ls -ln "$MOUNT_DIR" | head -5
ls -ld "$MOUNT_DIR/context_packs" || echo "WARN: context_packs not visible – check the Unraid share mapping"

step "5/7 $USER_NAME in group users (gid 100)"
usermod -aG users "$USER_NAME"
id "$USER_NAME"

step "6/7 linger for user timers"
loginctl enable-linger "$USER_NAME"

step "7/7 SSH key for Claude Code"
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$USER_HOME/.ssh"
touch "$USER_HOME/.ssh/authorized_keys"
grep -qF "$CLAUDE_PUBKEY" "$USER_HOME/.ssh/authorized_keys" || echo "$CLAUDE_PUBKEY" >> "$USER_HOME/.ssh/authorized_keys"
chown "$USER_NAME:$USER_NAME" "$USER_HOME/.ssh/authorized_keys"
chmod 600 "$USER_HOME/.ssh/authorized_keys"

echo
echo "done. Next (as $USER_NAME, no sudo): README sections 3-8 – Claude Code can do them over SSH."
echo "Write test of the share (as $USER_NAME, after a new login for the group change):"
echo "  touch $MOUNT_DIR/context_packs/vm_write_test.txt && rm $MOUNT_DIR/context_packs/vm_write_test.txt"
