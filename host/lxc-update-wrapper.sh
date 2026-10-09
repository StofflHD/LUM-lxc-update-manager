#!/usr/bin/env bash
# lxc-update-wrapper - runs on the Proxmox host.
#
# Used as SSH "forced command" so the update manager can only execute the
# verbs below, never an arbitrary shell. Install:
#   install -m 0755 lxc-update-wrapper.sh /usr/local/bin/lxc-update-wrapper
# and in /root/.ssh/authorized_keys:
#   command="/usr/local/bin/lxc-update-wrapper",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA... lxc-update-manager
#
# Works for LXC containers (pct) and QEMU VMs (qm). Inside a VM commands run
# through the QEMU guest agent, which must be installed and enabled.
#
# Verbs:
#   version                   version of this script (WRAPPER_VERSION)
#   list                      JSON list of all containers and VMs on this node (with "type")
#   info     <vmid>           pkg=<apt|apk|unknown> / community=<0|1> / script=<ct script name>
#   check    <vmid>           one upgradable package per line
#   upgrade  <vmid>           OS upgrade (streams output; for VMs at the end)
#   app-version <vmid> <app>  installed app version (~/.<app>, written by the check_for_* / fetch_and_deploy_* helpers)
#   pkg-version <vmid> <pip|npm> <package>  installed version of a pip / npm package
#   app-update <vmid>         community-scripts "update" in silent mode (PHS_SILENT=1), LXC only
#                             exit 75 = needs interactive mode, 113 = under-provisioned,
#                             114 = /boot storage low
#
#   snapshot  <vmid> <name>            create snapshot (name must start with lum_)
#   snapshots <vmid>                   JSON list of the guest's lum_ snapshots
#   prune-snapshots <vmid> <keep>      delete all but the <keep> newest lum_ snapshots
#   delete-snapshot <vmid> <name>      delete one lum_ snapshot
#   rollback  <vmid> <name>            roll back to a lum_ snapshot (stops/starts the guest)
#   backup    <vmid> <storage> <mode>  vzdump with marker note, mode snapshot|suspend|stop
#   prune-backups <vmid> <storage> <keep>  delete all but the <keep> newest marked backups
#   backups   <vmid>                   JSON list of the guest's marked backups on all storages
#   delete-backup  <vmid> <id>         delete one marked backup (id = its ctime)
#   restore-backup <vmid> <id>         restore the guest from a marked backup (stops/starts it)
#
# The manager can only ever touch snapshots named lum_* and backups whose note
# is exactly "lxc-update-manager" - never your own snapshots or backup jobs.
# Containers and VMs tagged "no-lum" are refused completely.

set -euo pipefail

# bump when verbs are added or changed; LUM checks it and asks for a host update
WRAPPER_VERSION=6
# guests with this Proxmox tag are off limits for LUM (every verb except list/version)
EXCLUDE_TAG="no-lum"
MARKER="lxc-update-manager"

# SSH_ORIGINAL_COMMAND when called via SSH, "$*" for local testing.
read -r -a ARGS <<< "${SSH_ORIGINAL_COMMAND:-$*}"
VERB="${ARGS[0]:-}"
VMID="${ARGS[1]:-}"
NODE="$(hostname)"

# set by require_vmid: pct/lxc for containers, qm/qemu for VMs
TOOL=""
PVE_TYPE=""
GUEST=""

die() { echo "error: $*" >&2; exit 2; }

require_vmid() {
  [[ "$VMID" =~ ^[0-9]{3,9}$ ]] || die "invalid vmid"
  if pct config "$VMID" >/dev/null 2>&1; then
    TOOL=pct PVE_TYPE=lxc GUEST=container
  elif qm config "$VMID" >/dev/null 2>&1; then
    TOOL=qm PVE_TYPE=qemu GUEST=VM
  else
    die "unknown vmid $VMID"
  fi
  if "$TOOL" config "$VMID" 2>/dev/null | sed -n 's/^tags: //p' | tr ';, ' '\n\n\n' | grep -qix "$EXCLUDE_TAG"; then
    echo "error: $GUEST $VMID is tagged $EXCLUDE_TAG - LUM does not manage it" >&2
    exit 6
  fi
}

is_running() { "$TOOL" status "$VMID" | grep -q running; }

require_running() {
  is_running || { echo "error: $GUEST $VMID not running" >&2; exit 3; }
  if [[ $TOOL == qm ]] && ! qm guest cmd "$VMID" ping >/dev/null 2>&1; then
    echo "error: QEMU guest agent not reachable in VM $VMID - install qemu-guest-agent in the VM and enable 'QEMU Guest Agent' in its Proxmox options" >&2
    exit 5
  fi
}

require_snapname() {
  [[ "$1" =~ ^lum_[0-9_]{1,30}$ ]] || die "invalid snapshot name (must be lum_<digits>)"
}

require_keep() {
  [[ "$1" =~ ^[0-9]{1,3}$ ]] || die "invalid keep count"
}

require_storage() {
  [[ "$1" =~ ^[A-Za-z][A-Za-z0-9_.-]{0,40}$ ]] || die "invalid storage name"
  pvesm status --storage "$1" >/dev/null 2>&1 || die "unknown storage $1"
}

# vzdump backups of $VMID made by LUM (note = marker) on every active backup
# storage, newest first: [{"id": ctime, "volid", "storage", "ctime", "size", "protected"}]
lum_backups_json() {
  local s
  for s in $(pvesm status --content backup 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}'); do
    pvesh get "/nodes/$NODE/storage/$s/content" --content backup --vmid "$VMID" --output-format json 2>/dev/null || true
  done | perl -MJSON::PP -e '
    my $marker = shift;
    my $json = JSON::PP->new;
    local $/; $json->incr_parse(<STDIN> // "");
    my @out;
    while (my $list = eval { $json->incr_parse }) {
      for my $x (@$list) {
        next unless ($x->{notes} // "") =~ /^\Q$marker\E\s*$/;
        my ($storage) = $x->{volid} =~ /^([^:]+):/;
        push @out, { id => $x->{ctime}, volid => $x->{volid}, storage => $storage,
                     ctime => $x->{ctime}, size => $x->{size}, protected => ($x->{protected} ? 1 : 0) };
      }
    }
    print JSON::PP->new->canonical->encode([ sort { $b->{ctime} <=> $a->{ctime} } @out ]);
  ' "$MARKER"
}

# volid (and protected flag) of the marked backup with ctime $1; exactly one must match
lum_backup_by_id() {
  [[ "$1" =~ ^[0-9]{9,11}$ ]] || die "invalid backup id"
  lum_backups_json | perl -MJSON::PP -e '
    my $id = shift; local $/;
    my @m = grep { $_->{ctime} == $id } @{ decode_json(<STDIN>) };
    exit 3 unless @m == 1;
    print "$m[0]{volid} $m[0]{protected}\n";
  ' "$1" || die "no single LUM backup with id $1 for $VMID"
}

# lum_ snapshots of $VMID as JSON, newest first
lum_snapshots_json() {
  pvesh get "/nodes/$NODE/$PVE_TYPE/$VMID/snapshot" --output-format json | perl -MJSON::PP -0 -e '
    my $d = decode_json(<STDIN>);
    my @s = sort { ($b->{snaptime} // 0) <=> ($a->{snaptime} // 0) }
            grep { $_->{name} =~ /^lum_/ } @$d;
    print JSON::PP->new->canonical->encode([ map { { name => $_->{name}, snaptime => $_->{snaptime} } } @s ]);
  '
}

# C.UTF-8 exists in every Debian/Ubuntu guest; the host's own LANG
# (e.g. en_US.UTF-8) usually doesn't, and apt/perl warn about it on every call
guest_env=(env LANG=C.UTF-8 LC_ALL=C.UTF-8)

# run a shell script in the guest: in_guest <timeout-seconds> <script>
# LXC: pct exec (streams). VM: guest agent, output arrives when the command ends.
in_guest() {
  local timeout=$1 script=$2
  if [[ $TOOL == pct ]]; then
    pct exec "$VMID" -- "${guest_env[@]}" sh -c "$script"
    return
  fi
  qm guest exec "$VMID" --timeout "$timeout" -- "${guest_env[@]}" sh -c "$script" | perl -MJSON::PP -0 -e '
    my $raw = <STDIN>;
    my $r = eval { decode_json($raw) } or do { print STDERR "unexpected answer from the guest agent\n"; exit 1 };
    if (!$r->{exited}) {
      print STDERR "still running in the VM after the timeout (guest pid $r->{pid})\n";
      exit 124;
    }
    print $r->{"out-data"} // "";
    print STDERR $r->{"err-data"} // "";
    print STDERR "(output truncated by the guest agent)\n" if $r->{"out-truncated"} || $r->{"err-truncated"};
    exit($r->{exitcode} // 1);
  '
}

case "$VERB" in
  version)
    echo "$WRAPPER_VERSION"
    ;;

  list)
    # containers and VMs in one list, templates left out
    LXC_JSON=$(pvesh get "/nodes/$NODE/lxc" --output-format json) \
    QEMU_JSON=$(pvesh get "/nodes/$NODE/qemu" --output-format json) \
    perl -MJSON::PP -e '
      my @all;
      for my $t (["lxc", $ENV{LXC_JSON}], ["qemu", $ENV{QEMU_JSON}]) {
        for my $g (@{ decode_json($t->[1] || "[]") }) {
          next if $g->{template};
          $g->{type} = $t->[0];
          push @all, $g;
        }
      }
      print JSON::PP->new->canonical->encode(\@all);
    '
    ;;

  info)
    require_vmid; require_running
    in_guest 120 '
      if command -v apt-get >/dev/null 2>&1; then echo pkg=apt
      elif command -v apk >/dev/null 2>&1; then echo pkg=apk
      else echo pkg=unknown; fi
      # the LUM container itself has an "update" too (marker LUM_SELF_UPDATE) - not an app
      if [ -x /usr/bin/update ] && ! grep -q LUM_SELF_UPDATE /usr/bin/update; then
        echo community=1
        # same lookup order as community-scripts tools/pve/update-apps.sh
        s=$(sed -n -E "s/^[[:space:]]*export[[:space:]]+UPDATE_SCRIPT_NAME=[^a-zA-Z0-9._-]?([a-zA-Z0-9._-]+).*/\1/p" /usr/bin/update | head -n1)
        [ -z "$s" ] && s=$(grep -oE "/ct/[a-zA-Z0-9._-]+\.sh" /usr/bin/update | head -n1 | sed "s|.*/ct/||; s|\.sh$||")
        echo "script=$s"
      else
        echo community=0
      fi
    '
    ;;

  app-version)
    require_vmid; require_running
    APP="${ARGS[2]:-}"
    [[ "$APP" =~ ^[a-z0-9._-]{1,60}$ ]] || die "invalid app name"
    in_guest 60 "cat \"\$HOME/.$APP\" 2>/dev/null || true"
    ;;

  pkg-version)
    # installed version of a pip or npm package (apps updated with pip / npm)
    require_vmid; require_running
    MGR="${ARGS[2]:-}"
    PKG="${ARGS[3]:-}"
    [[ "$MGR" =~ ^(pip|npm)$ ]] || die "invalid package manager"
    [[ "$PKG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$ ]] || die "invalid package name"
    if [[ $MGR == pip ]]; then
      in_guest 60 "for p in pip3 pip 'python3 -m pip'; do v=\$(\$p show $PKG 2>/dev/null | sed -n 's/^Version: //p'); [ -n \"\$v\" ] && { echo \"\$v\"; exit 0; }; done; true"
    else
      in_guest 60 "npm ls -g --depth=0 $PKG 2>/dev/null | grep -oE '$PKG@[^ ]+' | head -n1 | sed 's/.*@//'; true"
    fi
    ;;

  check)
    require_vmid; require_running
    in_guest 600 '
      if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq >/dev/null 2>&1 || true
        apt list --upgradable 2>/dev/null | tail -n +2
      elif command -v apk >/dev/null 2>&1; then
        apk update -q >/dev/null 2>&1 || true
        apk version -l "<" 2>/dev/null | tail -n +2
      fi
    '
    ;;

  upgrade)
    require_vmid; require_running
    [[ $TOOL == qm ]] && echo "running in VM $VMID through the QEMU guest agent - the output appears when the update has finished"
    in_guest 7200 '
      if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        # no changelog reading/mailing during an unattended upgrade
        export APT_LISTCHANGES_FRONTEND=none
        apt-get update &&
        apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold dist-upgrade &&
        apt-get -y autoremove
      elif command -v apk >/dev/null 2>&1; then
        apk -U upgrade
      else
        echo "unsupported package manager" >&2; exit 4
      fi
    ' 2>&1
    ;;

  app-update)
    # PHS_SILENT=1 is the official unattended mode, the same call
    # community-scripts' own update-apps.sh makes. stdin from /dev/null so no
    # prompt can block a run nobody is watching.
    require_vmid; require_running
    [[ $TOOL == pct ]] || die "app updates are only supported for LXC containers"
    pct exec "$VMID" -- "${guest_env[@]}" sh -c '[ -x /usr/bin/update ] || { echo "no community-scripts update command"; exit 4; }; export PHS_SILENT=1; update' </dev/null 2>&1
    ;;

  snapshot)
    require_vmid
    NAME="${ARGS[2]:-}"; require_snapname "$NAME"
    "$TOOL" snapshot "$VMID" "$NAME" --description "created by $MARKER" 2>&1
    ;;

  snapshots)
    require_vmid
    lum_snapshots_json
    ;;

  prune-snapshots)
    require_vmid
    KEEP="${ARGS[2]:-}"; require_keep "$KEEP"
    lum_snapshots_json | perl -MJSON::PP -0 -e '
      my $keep = shift; my @s = @{ decode_json(<STDIN>) };
      print "$_->{name}\n" for @s[$keep .. $#s];
    ' "$KEEP" | while read -r name; do
      "$TOOL" delsnapshot "$VMID" "$name" 2>&1
      echo "deleted snapshot $name"
    done
    ;;

  delete-snapshot)
    require_vmid
    NAME="${ARGS[2]:-}"; require_snapname "$NAME"
    lum_snapshots_json | grep -q "\"$NAME\"" || die "snapshot $NAME does not exist (anymore)"
    "$TOOL" delsnapshot "$VMID" "$NAME" 2>&1
    echo "deleted snapshot $NAME"
    ;;

  rollback)
    require_vmid
    NAME="${ARGS[2]:-}"; require_snapname "$NAME"
    lum_snapshots_json | grep -q "\"$NAME\"" || die "snapshot $NAME does not exist (anymore)"
    WAS_RUNNING=0
    if is_running; then
      WAS_RUNNING=1
      echo "shutting down $GUEST $VMID"
      "$TOOL" shutdown "$VMID" --timeout 120 2>&1 || "$TOOL" stop "$VMID" 2>&1
    fi
    echo "rolling back to $NAME"
    "$TOOL" rollback "$VMID" "$NAME" 2>&1
    if [[ $WAS_RUNNING == 1 ]]; then
      echo "starting $GUEST $VMID"
      "$TOOL" start "$VMID" 2>&1
    fi
    echo "rollback done"
    ;;

  backup)
    require_vmid
    STORAGE="${ARGS[2]:-}"; require_storage "$STORAGE"
    MODE="${ARGS[3]:-snapshot}"
    [[ "$MODE" =~ ^(snapshot|suspend|stop)$ ]] || die "invalid backup mode"
    OPTS=(--storage "$STORAGE" --mode "$MODE" --notes-template "$MARKER")
    # PBS does its own compression and rejects --compress
    TYPE=$(pvesh get "/storage/$STORAGE" --output-format json | perl -MJSON::PP -0 -e 'print decode_json(<STDIN>)->{type}')
    [[ "$TYPE" == "pbs" ]] || OPTS+=(--compress zstd)
    vzdump "$VMID" "${OPTS[@]}" 2>&1
    ;;

  backups)
    require_vmid
    lum_backups_json
    ;;

  delete-backup)
    require_vmid
    read -r VOLID PROTECTED < <(lum_backup_by_id "${ARGS[2]:-}")
    [[ $PROTECTED == 1 ]] && die "backup $VOLID is protected - remove the protection in Proxmox first"
    pvesm free "$VOLID" 2>&1
    echo "deleted backup $VOLID"
    ;;

  restore-backup)
    require_vmid
    read -r VOLID _ < <(lum_backup_by_id "${ARGS[2]:-}")
    WAS_RUNNING=0
    if is_running; then
      WAS_RUNNING=1
      echo "shutting down $GUEST $VMID"
      "$TOOL" shutdown "$VMID" --timeout 120 2>&1 || "$TOOL" stop "$VMID" 2>&1
    fi
    echo "restoring $GUEST $VMID from $VOLID"
    if [[ $TOOL == pct ]]; then
      # keep today's storage and privilege level: pct restore would default to
      # storage "local" and a privileged container
      CONF=$(pct config "$VMID")
      ROOT_STORAGE=$(sed -n 's/^rootfs: \([^:,]*\):.*/\1/p' <<<"$CONF")
      UNPRIV=$(sed -n 's/^unprivileged: //p' <<<"$CONF")
      pct restore "$VMID" "$VOLID" --force 1 --storage "${ROOT_STORAGE:-local-lvm}" --unprivileged "${UNPRIV:-0}" 2>&1
    else
      # VM disks go back to their original storages
      qmrestore "$VOLID" "$VMID" --force 1 2>&1
    fi
    if [[ $WAS_RUNNING == 1 ]]; then
      echo "starting $GUEST $VMID"
      "$TOOL" start "$VMID" 2>&1
    fi
    echo "restore done"
    ;;

  prune-backups)
    require_vmid
    STORAGE="${ARGS[2]:-}"; require_storage "$STORAGE"
    KEEP="${ARGS[3]:-}"; require_keep "$KEEP"
    pvesh get "/nodes/$NODE/storage/$STORAGE/content" --content backup --vmid "$VMID" --output-format json |
    perl -MJSON::PP -0 -e '
      my ($keep, $marker) = @ARGV;
      my @b = sort { $b->{ctime} <=> $a->{ctime} }
              grep { ($_->{notes} // "") =~ /^\Q$marker\E\s*$/ && !$_->{protected} } @{ decode_json(<STDIN>) };
      print "$_->{volid}\n" for @b[$keep .. $#b];
    ' "$KEEP" "$MARKER" | while read -r volid; do
      pvesm free "$volid" 2>&1
      echo "deleted backup $volid"
    done
    ;;

  *)
    die "verb not allowed: '$VERB'"
    ;;
esac
