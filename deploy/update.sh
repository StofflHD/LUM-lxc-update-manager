#!/usr/bin/env bash
# LUM_SELF_UPDATE - installed as /usr/bin/update in the LUM container.
#
#   update            update LUM to the latest version from GitHub
#   update --check    only show whether an update is available
#   update --force    reinstall even if the version is the same
#
# Updates the app inside this container (settings, login, key and history
# are kept). The host script on the Proxmox host can't be updated from here
# on purpose - the container may only run its fixed verbs there. If it is
# outdated, this script tells you the command to run on the host.
set -euo pipefail

REPO="${LUM_REPO:-StofflHD/LUM-lxc-update-manager}"
REF="${LUM_REF:-main}"
DEST=/opt/lxc-update-manager
PORT=8080
HOST_ONELINER="bash <(curl -fsSL https://raw.githubusercontent.com/$REPO/$REF/install.sh) --update"

RD=$'\e[31m' GN=$'\e[32m' YW=$'\e[33m' BL=$'\e[36m' BD=$'\e[1m' CL=$'\e[0m'
info() { echo -e "${BL}➜${CL} $*"; }
ok()   { echo -e "${GN}✔${CL} $*"; }
warn() { echo -e "${YW}⚠${CL} $*"; }
die()  { echo -e "${RD}✘ $*${CL}" >&2; exit 1; }

CHECK=0
FORCE=0
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;;
    --force) FORCE=1 ;;
    -h | --help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $a (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Please run as root"
[[ -d $DEST/app ]] || die "LUM is not installed in $DEST"

# value of KEY from .env (empty if unset)
env_value() { grep -m1 "^$1=" "$DEST/.env" 2>/dev/null | cut -d= -f2- || true; }

# relative paths in .env are relative to $DEST
abs_path() { [[ $1 == /* ]] && echo "$1" || echo "$DEST/$1"; }

# Compare the host script version on the Proxmox host with the one this
# LUM version needs. Old host scripts don't know "version" -> treated as 0.
check_host_script() {
  local need have host port user key known
  need=$(grep -m1 '^WRAPPER_VERSION=' "$DEST/host/lxc-update-wrapper.sh" | cut -d= -f2)
  host=$(env_value LUM_PVE_HOST)
  port=$(env_value LUM_PVE_PORT)
  user=$(env_value LUM_PVE_USER)
  key=$(abs_path "$(env_value LUM_SSH_KEY_PATH)")
  known=$(abs_path "$(env_value LUM_KNOWN_HOSTS_PATH)")
  [[ -n $host && -n $need ]] || return 0
  have=$(ssh -i "$key" -o UserKnownHostsFile="$known" -o BatchMode=yes -o ConnectTimeout=10 \
    -p "${port:-22}" "${user:-root}@$host" version 2>/dev/null | tr -dc '0-9' || true)
  have=${have:-0}
  if ((have < need)); then
    warn "The host script on the Proxmox host is outdated (version $have, needs $need)."
    warn "Run this on the Proxmox host as root:"
    echo "    $HOST_ONELINER"
  else
    ok "Host script on the Proxmox host is up to date (version $have)"
  fi
}

installed=$(tr -d '[:space:]' <"$DEST/VERSION" 2>/dev/null || true)
installed=${installed:-unknown}
latest=$(curl -fsSL --max-time 20 "https://raw.githubusercontent.com/$REPO/$REF/VERSION" | tr -d '[:space:]') ||
  die "Cannot reach GitHub ($REPO)"
[[ $latest =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Unexpected version from GitHub: '$latest'"
info "Installed: ${BD}$installed${CL}, latest: ${BD}$latest${CL}"

if [[ $installed == "$latest" && $FORCE == 0 ]]; then
  ok "LUM is up to date"
  check_host_script
  exit 0
fi
if [[ $CHECK == 1 ]]; then
  info "Update available: $installed → $latest (run: update)"
  exit 0
fi

tmp=$(mktemp -d /tmp/lum-update.XXXXXX)
trap 'rm -rf "$tmp"' EXIT

info "Downloading $REPO ($REF) …"
curl -fsSL --max-time 120 "https://codeload.github.com/$REPO/tar.gz/$REF" | tar -xz -C "$tmp" --strip-components=1 ||
  die "Download failed"
[[ -f $tmp/deploy/install.sh && -f $tmp/app/main.py ]] || die "Downloaded archive is incomplete"

if [[ -f $DEST/data/lum.db ]]; then
  cp -p "$DEST/data/lum.db" "$DEST/data/lum.db.before-update"
  ok "Database backed up to data/lum.db.before-update"
fi

info "Installing (takes up to a minute) …"
log=$(mktemp)
if ! bash "$tmp/deploy/install.sh" --quiet >"$log" 2>&1; then
  tail -n 25 "$log" >&2
  die "Installation failed (log: $log)"
fi
rm -f "$log"

systemctl restart lxc-update-manager
for _ in $(seq 1 30); do
  if curl -fs --max-time 2 "http://127.0.0.1:$PORT/api/auth/state" >/dev/null; then
    ok "LUM updated to ${BD}$(tr -d '[:space:]' <"$DEST/VERSION")${CL} and running"
    check_host_script
    exit 0
  fi
  sleep 2
done
journalctl -u lxc-update-manager -n 30 --no-pager >&2 || true
die "LUM does not answer after the update (see the log above)"
