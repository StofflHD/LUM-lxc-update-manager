# Using LUM

[← Back to the README](../README.md)

## Using the web UI

| Where | Control | What it does |
|---|---|---|
| Top right | **Refresh list** | Re-reads only the list of containers/VMs (fast). New running guests are checked right away; the result is shown below the status line. |
| | **Check all** | Re-reads the list and checks every running guest for OS and app updates. Also runs automatically every `LUM_CHECK_INTERVAL_MINUTES`. |
| | **☰** menu | **Theme** (System / Light / Dark, stored in the browser), **Settings** (see [Configuration](configuration.md#in-the-web-ui)) and **Change password**. |
| Each row | **Check** | Checks this one guest. |
| | **OS update** | Opens the update dialog (with the backup checkbox), then runs apt/apk with a live log. |
| | **App update** | Community-script app update (containers only; not shown for VMs). Highlighted when a newer app version exists. |
| | **Snapshots** | The guest's LUM snapshots with **Rollback** and **Delete**. |
| | `▸ N packages` | Click to list the pending packages. |
| History | **Rollback** · **Delete** | Roll back to / delete the snapshot made before that update. |
| | **Log** · **Remove** | Show the stored log / remove the entry (the snapshot is kept). |
| | **Clear history** | Removes all finished entries (running updates and snapshots stay). |

Below 1100 px window width every row turns into a card. The footer shows the version.
If the host script on the Proxmox host is older than this LUM version needs, a yellow
note below the status line says so and shows the command to update it.

## App updates

Containers created with the community scripts have an `update` command. LUM reads the
installed app version (`~/.<app>` in the container) and the latest release of the app's
GitHub repository (from `check_for_gh_release` in the matching `ct/<app>.sh`) and runs the
update in the official silent mode (`PHS_SILENT=1`, the same call the community scripts'
`tools/pve/update-apps.sh` makes).

In silent mode the community script deliberately stops in these cases:

| Exit | Meaning | Fix |
|---|---|---|
| 75 | The update needs interactive mode | Run `update` manually inside the container |
| 113 | The container has less CPU/RAM than the script requires | Increase resources (e.g. Tandoor: 4 CPU / 4 GB) |
| 114 | `/boot` is more than 80 % full | Free up space |

Apps without `check_for_gh_release` (e.g. AdGuard, Home Assistant) show *Version unknown*
but can still be updated.

## VMs

OS updates, checks, snapshots, vzdump backups and rollback work for VMs too. Proxmox can
only run commands inside a VM through the **QEMU guest agent**, so each VM needs:

1. the agent installed and running in the VM, e.g. Debian/Ubuntu:
   `apt install qemu-guest-agent && systemctl enable --now qemu-guest-agent`
2. **QEMU Guest Agent** enabled in Proxmox (VM → Options), then a full VM shutdown and start
   (a reboot from inside the VM is not enough).

Without it the VM shows **no guest agent** (the tooltip explains what's missing).
Supported are Linux VMs with apt or apk. The agent returns the output only when a command
has finished, so the log of a VM update appears at the end. App updates are LXC only.

## Backup, cleanup and rollback

| `LUM_BACKUP_MODE` | What happens before the update | Cleanup |
|---|---|---|
| `snapshot` (default) | snapshot named `lum_<date>_<time>` (`pct`/`qm snapshot`) | keeps `LUM_SNAPSHOT_KEEP` (default 2) per guest |
| `vzdump` | Backup to `LUM_BACKUP_STORAGE` (dir, NFS, CIFS or PBS), note `lxc-update-manager` | keeps `LUM_BACKUP_KEEP` (default 2) per guest |
| `none` | nothing | – |

- If the backup fails, the update does **not** run.
- The update dialog has a checkbox to skip the backup for that one update. Without a
  backup the update cannot be rolled back.
- Cleanup only runs after a successful (or deliberately skipped) update. After a failure
  every backup is kept.
- The manager **only** touches its own backups: snapshots named `lum_*` and backups with
  the note `lxc-update-manager`. The host script enforces this, not just the app.
  Protected backups are never deleted.
- Snapshots need storage with snapshot support (LVM-thin, ZFS, Ceph, qcow2). On plain
  directory storage use `vzdump`.
- **Rollback** shuts the guest down, rolls it back and starts it again if it was running.
  On **ZFS** this only works for the newest snapshot: Proxmox refuses the rollback while
  newer snapshots exist.
- A vzdump backup is restored in the Proxmox UI (guest → Backup → Restore), not in LUM.

## Login

The web UI has one admin account. The installer asks for username and password
(leave the password empty to get a random one, shown at the end).

- Change the password: menu **☰** → **Change password**, or inside the container
  `cd /opt/lxc-update-manager && venv/bin/python -m app.passwd [username]`
  (also if you forgot it). Takes effect immediately and logs out all other sessions.
- The password is stored scrypt-hashed in `data/auth.json`, never in plain text.
- Session: signed cookie (HttpOnly, SameSite=Strict), valid for `LUM_SESSION_HOURS` (12 h).
- After 5 failed attempts the client IP is locked for 15 minutes (behind a reverse proxy
  see [`FORWARDED_ALLOW_IPS`](reverse-proxy.md)).
- Protection against other websites (CSRF): API calls that change something need the
  header `X-Requested-With: lum`, WebSockets check the origin.
- The connection itself is plain **HTTP** on port 8080. For access from outside your home
  network use a [reverse proxy](reverse-proxy.md) with HTTPS.
