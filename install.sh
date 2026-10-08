#!/usr/bin/env bash
# LXC Update Manager - easy installer, runs on the Proxmox host.
#
# Straight from GitHub:
#   bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh)
# or from a local copy of the project: bash install.sh
#
#   bash install.sh              install: creates the LXC and sets everything up
#   bash install.sh --update     update app + host wrapper, keeps settings and data
#   bash install.sh --uninstall  remove SSH access + wrapper (optionally the container)
#   -y / --yes                   no questions, use defaults / preset variables
#
# Every question can be preset, e.g. unattended:
#   LUM_CTID=150 LUM_BACKUP_MODE=vzdump LUM_BACKUP_STORAGE=pbs bash install.sh -y
# GitHub:    LUM_REPO (default StofflHD/LUM-lxc-update-manager) LUM_REF (branch/tag, default main)
# Variables: LUM_CTID LUM_HOSTNAME LUM_ROOTFS_STORAGE LUM_TEMPLATE_STORAGE LUM_BRIDGE
#            LUM_NET (dhcp | 192.168.1.50/24) LUM_GW LUM_HOST_IP
#            LUM_BACKUP_MODE (snapshot|vzdump|none) LUM_BACKUP_STORAGE
#            LUM_USER LUM_PASSWORD (web UI login; with -y and no password a random one is generated)
#            LUM_ROOT_PASSWORD (container root password; with -y and unset the container has none)
set -euo pipefail

APP_DIR=/opt/lxc-update-manager
WRAPPER=/usr/local/bin/lxc-update-wrapper
AUTH_KEYS=/root/.ssh/authorized_keys
MARK=lxc-update-manager
PORT=8080
LOG=/var/log/lxc-update-manager-install.log

REPO="${LUM_REPO:-StofflHD/LUM-lxc-update-manager}"
REF="${LUM_REF:-main}"

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIG_ARGS=("$@")

RD=$'\e[31m' GN=$'\e[32m' YW=$'\e[33m' BL=$'\e[36m' BD=$'\e[1m' CL=$'\e[0m'
info() { echo -e "${BL}➜${CL} $*"; }
ok()   { echo -e "${GN}✔${CL} $*"; }
warn() { echo -e "${YW}⚠${CL} $*"; }
die()  { echo -e "${RD}✘ $*${CL}" >&2; exit 1; }

YES=0
ACTION=install
for a in "$@"; do
  case "$a" in
    -y | --yes) YES=1 ;;
    --update) ACTION=update ;;
    --uninstall) ACTION=uninstall ;;
    -h | --help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $a (see --help)" ;;
  esac
done

# ask VAR "question" default - keeps a preset $VAR, otherwise asks (Enter = default)
ask() {
  local var=$1 question=$2 def=$3 answer
  [[ -n "${!var:-}" ]] && return 0
  if [[ $YES == 1 ]]; then
    printf -v "$var" '%s' "$def"
    return 0
  fi
  read -r -p "${YW}?${CL} $question [${BD}${def}${CL}]: " answer </dev/tty
  printf -v "$var" '%s' "${answer:-$def}"
}

confirm() {
  [[ $YES == 1 ]] && return 0
  local a
  read -r -p "${YW}?${CL} $1 [y/N]: " a </dev/tty
  [[ "$a" =~ ^[jJyY]$ ]]
}

# run a step, output goes to $LOG; on error show its tail
step() {
  local msg=$1
  shift
  info "$msg …"
  if ! "$@" >>"$LOG" 2>&1; then
    echo
    tail -n 25 "$LOG" >&2
    die "$msg failed (full log: $LOG)"
  fi
  ok "$msg"
}

# C.UTF-8: the host passes its own LANG (e.g. en_US.UTF-8), which the Debian
# template doesn't have - every apt/perl call would warn about it
ct_exec() { pct exec "$CTID" -- env LANG=C.UTF-8 LC_ALL=C.UTF-8 bash -c "$1"; }

GENERATED_PW=0

# web UI password: preset, typed twice, or random (empty input / -y)
ask_password() {
  [[ -n ${LUM_PASSWORD:-} ]] && return 0
  if [[ $YES == 1 ]]; then
    LUM_PASSWORD=$(openssl rand -hex 8)
    GENERATED_PW=1
    return 0
  fi
  local p1 p2
  while true; do
    read -r -s -p "${YW}?${CL} Password for the web UI (empty = generate a random one): " p1 </dev/tty
    echo
    if [[ -z $p1 ]]; then
      LUM_PASSWORD=$(openssl rand -hex 8)
      GENERATED_PW=1
      return 0
    fi
    if ((${#p1} < 8)); then
      warn "At least 8 characters"
      continue
    fi
    read -r -s -p "${YW}?${CL} Repeat password: " p2 </dev/tty
    echo
    if [[ $p1 == "$p2" ]]; then
      LUM_PASSWORD=$p1
      return 0
    fi
    warn "Passwords do not match"
  done
}

# container root password: preset, typed twice, or none (empty input / -y)
ROOT_PW_SET=0
ask_root_password() {
  if [[ -n ${LUM_ROOT_PASSWORD:-} ]]; then
    ((${#LUM_ROOT_PASSWORD} >= 8)) || die "LUM_ROOT_PASSWORD needs at least 8 characters"
    ROOT_PW_SET=1
    return 0
  fi
  [[ $YES == 1 ]] && return 0
  local p1 p2
  while true; do
    read -r -s -p "${YW}?${CL} Root password for the container (empty = none, access via 'pct enter'): " p1 </dev/tty
    echo
    [[ -z $p1 ]] && return 0
    if ((${#p1} < 8)); then
      warn "At least 8 characters"
      continue
    fi
    read -r -s -p "${YW}?${CL} Repeat root password: " p2 </dev/tty
    echo
    if [[ $p1 == "$p2" ]]; then
      LUM_ROOT_PASSWORD=$p1
      ROOT_PW_SET=1
      return 0
    fi
    warn "Passwords do not match"
  done
}

# via stdin to chpasswd, not pct create --password (would show up in ps)
set_root_password() {
  printf 'root:%s\n' "$LUM_ROOT_PASSWORD" | pct exec "$CTID" -- chpasswd
}

# password goes in via stdin, never as an argument (would show up in ps / the log)
set_login() {
  printf '%s\n' "$LUM_PASSWORD" |
    pct exec "$CTID" -- env LANG=C.UTF-8 LC_ALL=C.UTF-8 \
      bash -c "cd $APP_DIR && venv/bin/python -m app.passwd --stdin '$LUM_USER'"
}

print_login() {
  if [[ $GENERATED_PW == 1 ]]; then
    echo -e "  Login:         ${BD}$LUM_USER${CL} / ${BD}$LUM_PASSWORD${CL}  ${YW}(write it down now, it is not stored)${CL}"
  else
    echo -e "  Login:         ${BD}$LUM_USER${CL} with your password"
  fi
  echo "  New password:  in the web UI (Password) or"
  echo "                 pct exec $CTID -- bash -c 'cd $APP_DIR && venv/bin/python -m app.passwd'"
}

storages() { pvesm status --content "$1" 2>/dev/null | awk 'NR>1 && $3=="active" {print $1, $2}'; }

# first storage of content type $1, preferring the name $2 / the type $3
pick_storage() {
  local list
  list=$(storages "$1")
  { echo "$list" | awk -v n="${2:-}" '$1==n {print $1}'
    echo "$list" | awk -v t="${3:-}" '$2==t {print $1}'
    echo "$list" | awk '{print $1}'
  } | grep -m1 . || true
}

require_storage() {
  pvesm status --storage "$1" >/dev/null 2>&1 || die "Storage '$1' does not exist"
}

installed_ctids() {
  [[ -f $AUTH_KEYS ]] || return 0
  grep -o "$MARK-ct[0-9]*" "$AUTH_KEYS" 2>/dev/null | sed 's/.*-ct//' | sort -u
}

# --- building blocks --------------------------------------------------------

install_wrapper() {
  install -m 0755 "$SRC/host/lxc-update-wrapper.sh" "$WRAPPER"
}

pick_template() {
  pveam update >>"$LOG" 2>&1 || warn "pveam update failed, using the existing template list"
  local v t
  for v in 13 12; do
    t=$(pveam available --section system | awk '{print $2}' | grep -E "^debian-$v-standard_.*_amd64" | sort -V | tail -n1)
    if [[ -n $t ]]; then
      echo "$t"
      return 0
    fi
  done
  die "No Debian template found (pveam available --section system)"
}

create_ct() {
  local net="name=eth0,bridge=$LUM_BRIDGE,ip=$LUM_NET"
  [[ $LUM_NET != dhcp && -n ${LUM_GW:-} ]] && net+=",gw=$LUM_GW"
  pct create "$CTID" "$LUM_TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
    --hostname "$LUM_HOSTNAME" --cores 1 --memory 512 --swap 512 \
    --rootfs "$LUM_ROOTFS_STORAGE:4" --net0 "$net" \
    --unprivileged 1 --features nesting=1 --onboot 1 \
    --tags "$MARK" --description "LXC Update Manager (web UI port $PORT)"
  pct start "$CTID"
}

wait_for_network() {
  local i
  for i in $(seq 1 60); do
    ct_exec "getent hosts deb.debian.org" >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "Container $CTID has no network/DNS after 2 minutes" >&2
  return 1
}

# copy the project into the CT and run the in-container installer
push_app() {
  local tgz
  tgz=$(mktemp --suffix=.tar.gz)
  tar -czf "$tgz" -C "$SRC" --exclude=./.venv --exclude=./data --exclude=./.env \
    --exclude=__pycache__ --exclude=./.git .
  pct push "$CTID" "$tgz" /tmp/lum-src.tar.gz
  rm -f "$tgz"
  ct_exec "rm -rf /tmp/lum-src && mkdir -p /tmp/lum-src && tar -xzf /tmp/lum-src.tar.gz -C /tmp/lum-src \
    && bash /tmp/lum-src/deploy/install.sh --quiet; rc=\$?; rm -rf /tmp/lum-src /tmp/lum-src.tar.gz; exit \$rc"
}

authorize_key() {
  local pub line tmp
  pub=$(pct exec "$CTID" -- cat "$APP_DIR/data/id_ed25519.pub")
  [[ $pub == ssh-ed25519\ * ]] || { echo "No valid SSH key in the container" >&2; return 1; }
  line="command=\"$WRAPPER\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ${pub% *} $MARK-ct$CTID"
  mkdir -p "$(dirname "$AUTH_KEYS")"
  touch "$AUTH_KEYS"
  # Write through the file: on Proxmox authorized_keys is a symlink into
  # /etc/pve, and sed -i would replace that symlink with a plain file.
  tmp=$(mktemp)
  grep -v " $MARK-ct$CTID\$" "$AUTH_KEYS" >"$tmp" || true
  echo "$line" >>"$tmp"
  cat "$tmp" >"$AUTH_KEYS"
  rm -f "$tmp"
}

unauthorize_key() {
  [[ -f $AUTH_KEYS ]] || return 0
  local tmp
  tmp=$(mktemp)
  grep -v " $MARK-ct$1\$" "$AUTH_KEYS" >"$tmp" || true
  cat "$tmp" >"$AUTH_KEYS"
  rm -f "$tmp"
}

# pin the host keys directly instead of trusting a network scan
write_known_hosts() {
  local tmp f
  tmp=$(mktemp)
  for f in /etc/ssh/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ecdsa_key.pub /etc/ssh/ssh_host_rsa_key.pub; do
    [[ -f $f ]] && echo "$LUM_HOST_IP $(cut -d' ' -f1,2 "$f")" >>"$tmp"
  done
  pct push "$CTID" "$tmp" "$APP_DIR/data/known_hosts" --perms 600
  rm -f "$tmp"
}

write_env() {
  local tmp
  tmp=$(mktemp)
  cat >"$tmp" <<EOF
# written by install.sh on $(date '+%Y-%m-%d %H:%M') - all options: .env.example
LUM_PVE_HOST=$LUM_HOST_IP
LUM_PVE_PORT=22
LUM_PVE_USER=root
LUM_SSH_KEY_PATH=data/id_ed25519
LUM_KNOWN_HOSTS_PATH=data/known_hosts
LUM_DB_PATH=data/lum.db

LUM_CHECK_INTERVAL_MINUTES=360
LUM_MAX_PARALLEL_CHECKS=4
LUM_GITHUB_TOKEN=

LUM_BACKUP_MODE=$LUM_BACKUP_MODE
LUM_SNAPSHOT_KEEP=2
LUM_BACKUP_STORAGE=${LUM_BACKUP_STORAGE:-}
LUM_BACKUP_VZDUMP_MODE=snapshot
LUM_BACKUP_KEEP=2
EOF
  pct push "$CTID" "$tmp" "$APP_DIR/.env" --perms 600
  rm -f "$tmp"
}

test_connection() {
  ct_exec "ssh -i $APP_DIR/data/id_ed25519 -o UserKnownHostsFile=$APP_DIR/data/known_hosts \
    -o BatchMode=yes -o ConnectTimeout=10 root@$LUM_HOST_IP list" | grep -q '"vmid"'
}

# /api/auth/state is public; everything else answers 401 without a login,
# which urlopen would treat as a failure
wait_for_web() {
  local i
  for i in $(seq 1 30); do
    ct_exec "python3 -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/api/auth/state', timeout=2)\"" \
      >/dev/null 2>&1 && return 0
    sleep 2
  done
  ct_exec "journalctl -u lxc-update-manager -n 30 --no-pager" >&2 || true
  return 1
}

ct_ip() { pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}'; }

banner() {
  echo -e "\n${BD}LXC Update Manager${CL} – $1\n"
  echo "Log: $LOG" >"$LOG"
}

# --- actions ----------------------------------------------------------------

do_install() {
  banner "Install"

  ask LUM_CTID "Container ID" "$(pvesh get /cluster/nextid)"
  CTID=$LUM_CTID
  [[ $CTID =~ ^[0-9]+$ ]] || die "Invalid container ID"
  pct status "$CTID" >/dev/null 2>&1 && die "CT $CTID already exists. To update: bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update"

  ask LUM_HOSTNAME "Hostname" "lxc-update-manager"
  ask LUM_ROOTFS_STORAGE "Storage for the container" "$(pick_storage rootdir local-lvm lvmthin)"
  require_storage "$LUM_ROOTFS_STORAGE"
  ask LUM_TEMPLATE_STORAGE "Storage for the Debian template" "$(pick_storage vztmpl local)"
  require_storage "$LUM_TEMPLATE_STORAGE"
  ask LUM_BRIDGE "Network bridge" "vmbr0"
  ip link show "$LUM_BRIDGE" >/dev/null 2>&1 || die "Bridge '$LUM_BRIDGE' does not exist"
  ask LUM_NET "IP address (dhcp or e.g. 192.168.1.50/24)" "dhcp"
  [[ $LUM_NET == dhcp ]] || ask LUM_GW "Gateway" "$(ip -4 route show default | awk '{print $3; exit}')"

  local host_ip
  host_ip=$(ip -4 -o addr show dev "$LUM_BRIDGE" | awk '{print $4}' | cut -d/ -f1 | head -n1)
  ask LUM_HOST_IP "IP of this Proxmox host (for the SSH connection from the container)" "${host_ip:-$(hostname -I | awk '{print $1}')}"

  # Snapshots need LVM-thin, ZFS, Ceph or btrfs. Suggest vzdump when any
  # container storage on this node can't do snapshots.
  local def_mode=snapshot
  if storages rootdir | awk '{print $2}' | grep -qvE '^(lvmthin|zfspool|rbd|btrfs)$'; then
    def_mode=vzdump
  fi
  echo -e "\n  Backup before every update:"
  echo "    snapshot – fast, same storage (LVM-thin, ZFS, Ceph)"
  echo "    vzdump   – real backup to a backup storage (dir, NFS, PBS)"
  echo "    none     – no backup"
  ask LUM_BACKUP_MODE "Backup mode" "$def_mode"
  [[ $LUM_BACKUP_MODE =~ ^(snapshot|vzdump|none)$ ]] || die "Backup mode must be snapshot, vzdump or none"
  if [[ $LUM_BACKUP_MODE == vzdump ]]; then
    local def_bs
    def_bs=$(pick_storage backup "" pbs)
    [[ -n $def_bs ]] || die "No storage for backups found (content 'backup')"
    ask LUM_BACKUP_STORAGE "Backup storage" "$def_bs"
    require_storage "$LUM_BACKUP_STORAGE"
  fi

  echo
  ask LUM_USER "Username for the web UI" "admin"
  [[ $LUM_USER =~ ^[A-Za-z0-9._-]{1,40}$ ]] || die "Username: only letters, digits, . _ -"
  ask_password
  ask_root_password

  echo -e "\n${BD}Summary${CL}
  Container   $CTID ($LUM_HOSTNAME), 1 CPU, 512 MB RAM, 4 GB on $LUM_ROOTFS_STORAGE
  Network     $LUM_BRIDGE, $LUM_NET${LUM_GW:+, gateway $LUM_GW}
  Proxmox IP  $LUM_HOST_IP
  Backup      $LUM_BACKUP_MODE${LUM_BACKUP_STORAGE:+ → $LUM_BACKUP_STORAGE}
  Login       $LUM_USER
  Root login  $([[ $ROOT_PW_SET == 1 ]] && echo "password set" || echo "no password (pct enter $CTID)")
  Also        host script to $WRAPPER, restricted SSH key in $AUTH_KEYS\n"
  confirm "Start the installation?" || die "Aborted"
  echo

  step "Install host script" install_wrapper
  TEMPLATE=$(pick_template)
  if ! pveam list "$LUM_TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
    step "Download template $TEMPLATE" pveam download "$LUM_TEMPLATE_STORAGE" "$TEMPLATE"
  fi
  step "Create and start container $CTID" create_ct
  [[ $ROOT_PW_SET == 1 ]] && step "Set root password" set_root_password
  step "Wait for network" wait_for_network
  step "Install app (takes 1–3 minutes)" push_app
  step "Set up SSH access (host script only)" authorize_key
  step "Store host keys" write_known_hosts
  step "Write configuration" write_env
  step "Set up login" set_login
  step "Test connection container → host" test_connection
  step "Start service" ct_exec "systemctl restart lxc-update-manager"
  step "Check web UI" wait_for_web

  echo -e "\n${GN}${BD}Done!${CL} Web UI: ${BD}http://$(ct_ip):$PORT${CL}"
  print_login
  if [[ $ROOT_PW_SET == 1 ]]; then
    echo "  Root shell:    console or pct enter $CTID (root with your password)"
  else
    echo "  Root shell:    pct enter $CTID (no root password set; later: pct exec $CTID -- passwd)"
  fi
  echo "  Settings:      pct exec $CTID -- nano $APP_DIR/.env  (then: pct exec $CTID -- systemctl restart lxc-update-manager)"
  echo "  Update:        bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update"
  echo -e "  ${YW}The connection is unencrypted (HTTP) – use it only on your home network or behind an HTTPS reverse proxy.${CL}"
}

do_update() {
  banner "Update"
  local found
  found=$(installed_ctids | head -n1)
  ask LUM_CTID "Container ID of the installation" "${found:-}"
  CTID=$LUM_CTID
  pct config "$CTID" >/dev/null 2>&1 || die "CT $CTID does not exist"
  pct status "$CTID" | grep -q running || step "Start container $CTID" pct start "$CTID"
  LUM_HOST_IP=$(pct exec "$CTID" -- sh -c "grep -m1 '^LUM_PVE_HOST=' $APP_DIR/.env | cut -d= -f2")

  step "Update host script" install_wrapper
  step "Wait for network" wait_for_network
  step "Update app (settings and data are kept)" push_app
  local need_login=0
  if ! pct exec "$CTID" -- test -f "$APP_DIR/data/auth.json"; then
    need_login=1
    warn "The new version has a login for the web UI – please set it up now"
    ask LUM_USER "Username for the web UI" "admin"
    [[ $LUM_USER =~ ^[A-Za-z0-9._-]{1,40}$ ]] || die "Username: only letters, digits, . _ -"
    ask_password
    step "Set up login" set_login
  fi
  step "Check SSH access" authorize_key
  step "Test connection container → host" test_connection
  step "Restart service" ct_exec "systemctl restart lxc-update-manager"
  step "Check web UI" wait_for_web
  echo -e "\n${GN}${BD}Update done.${CL} http://$(ct_ip):$PORT"
  [[ $need_login == 1 ]] && print_login
  return 0
}

do_uninstall() {
  banner "Uninstall"
  local found
  found=$(installed_ctids | head -n1)
  ask LUM_CTID "Container ID of the installation" "${found:-}"
  CTID=$LUM_CTID
  [[ $CTID =~ ^[0-9]+$ ]] || die "Invalid container ID"

  step "Remove SSH access for CT $CTID" unauthorize_key "$CTID"
  if [[ -z $(installed_ctids) ]]; then
    step "Remove host script" rm -f "$WRAPPER"
  else
    warn "Host script stays, other installations use it: $(installed_ctids | tr '\n' ' ')"
  fi

  if pct config "$CTID" >/dev/null 2>&1; then
    # never preselected, also not with -y
    local a
    read -r -p "${YW}?${CL} Permanently delete container $CTID including its data? [y/N]: " a </dev/tty || a=n
    if [[ $a =~ ^[jJyY]$ ]]; then
      pct status "$CTID" | grep -q running && step "Stop container" pct stop "$CTID"
      step "Delete container $CTID" pct destroy "$CTID" --purge
    else
      info "Container $CTID is kept"
    fi
  fi
  echo
  info "Snapshots (lum_*) and backups (note '$MARK') of your other containers are kept."
  info "If needed: pct listsnapshot <ID> / pct delsnapshot <ID> <name>"
}

# Run via curl, so the project files aren't next to this script: download the
# repo and re-run the installer from there.
fetch_source() {
  local dir
  info "Downloading $REPO ($REF) from GitHub …"
  dir=$(mktemp -d /tmp/lum-src.XXXXXX)
  if ! curl -fsSL "https://codeload.github.com/$REPO/tar.gz/$REF" | tar -xz -C "$dir" --strip-components=1; then
    rm -rf "$dir"
    die "Download failed – check the internet connection, LUM_REPO and LUM_REF"
  fi
  [[ -f $dir/install.sh && -f $dir/app/main.py ]] || die "Downloaded archive is incomplete"
  ok "Source downloaded"
  LUM_FETCHED_SRC="$dir" exec bash "$dir/install.sh" "${ORIG_ARGS[@]}"
}

# --- main -------------------------------------------------------------------

[[ $EUID -eq 0 ]] || die "Please run as root"
command -v pveversion >/dev/null 2>&1 || die "This script runs on the Proxmox host, not inside a container"
if [[ $ACTION != uninstall ]]; then
  if [[ ! -f $SRC/app/main.py || ! -f $SRC/host/lxc-update-wrapper.sh || ! -f $SRC/deploy/install.sh ]]; then
    [[ -z ${LUM_FETCHED_SRC:-} ]] || die "Project files are missing in the downloaded archive"
    fetch_source
  fi
fi
# downloaded copy: remove it again when done
if [[ -n ${LUM_FETCHED_SRC:-} && $SRC == "$LUM_FETCHED_SRC" ]]; then
  trap 'rm -rf "$LUM_FETCHED_SRC"' EXIT
fi

# Copied over from Windows? CRLF line endings would break every script in the
# container and the wrapper on this host. Normalizing is idempotent, so always do it.
if [[ $ACTION != uninstall ]]; then
  find "$SRC/app" "$SRC/host" "$SRC/deploy" "$SRC/requirements.txt" "$SRC/.env.example" -type f \
    \( -name '*.sh' -o -name '*.py' -o -name '*.service' -o -name '*.txt' -o -name '*.js' \
    -o -name '*.css' -o -name '*.html' -o -name '.env.example' \) -exec sed -i 's/\r$//' {} +
fi

case $ACTION in
  install) do_install ;;
  update) do_update ;;
  uninstall) do_uninstall ;;
esac
