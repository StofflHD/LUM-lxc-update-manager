#!/usr/bin/env bash
# Installs or updates the update manager inside a Debian 12/13 LXC.
# Run as root from the project directory: bash deploy/install.sh [--quiet]
# Safe to re-run: keeps .env, the SSH key and the database in data/.
# The easy installer (install.sh on the Proxmox host) calls this with --quiet.
set -euo pipefail

DEST=/opt/lxc-update-manager
SRC="$(cd "$(dirname "$0")/.." && pwd)"
QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# curl: handy for debugging and updates inside the container, the Debian template lacks it
apt-get install -y -qq python3 python3-venv openssh-client curl >/dev/null

mkdir -p "$DEST"
# replace code, never data/ or .env
rm -rf "$DEST/app" "$DEST/host"
cp -r "$SRC/app" "$SRC/host" "$SRC/requirements.txt" "$DEST/"
[ -f "$DEST/.env" ] || cp "$SRC/.env.example" "$DEST/.env"
chmod 600 "$DEST/.env"
mkdir -p "$DEST/data"
chmod 700 "$DEST/data"

[ -x "$DEST/venv/bin/pip" ] || python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install -q --upgrade pip
"$DEST/venv/bin/pip" install -q -r "$DEST/requirements.txt"

if [ ! -f "$DEST/data/id_ed25519" ]; then
  ssh-keygen -q -t ed25519 -N "" -C "lxc-update-manager" -f "$DEST/data/id_ed25519"
fi

cp "$SRC/deploy/lxc-update-manager.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q lxc-update-manager

[[ $QUIET == 1 ]] && exit 0

cat <<EOF

Installation finished. Next steps:

1. Install the wrapper on the Proxmox host:
     install -m 0755 host/lxc-update-wrapper.sh /usr/local/bin/lxc-update-wrapper

2. Add this line to /root/.ssh/authorized_keys on the host:
     command="/usr/local/bin/lxc-update-wrapper",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $(cat "$DEST/data/id_ed25519.pub")

3. Store the host key (here in the LXC):
     ssh-keyscan <PROXMOX-IP> > $DEST/data/known_hosts

4. Edit $DEST/.env (LUM_PVE_HOST) and start:
     systemctl restart lxc-update-manager

Web UI: http://$(hostname -I | awk '{print $1}'):8080

Easier: run the installer on the Proxmox host:
  bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh)
EOF
