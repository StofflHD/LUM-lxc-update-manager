# Using LUM

[← Back to the README](../README.md)

## Using the web UI

| Where | Control | What it does |
|---|---|---|
| Top right | **Refresh list** | Re-reads only the list of containers/VMs (fast). New running guests are checked right away; the result is shown below the status line. |
| | **Check all** | Re-reads the list and checks every running guest for OS and app updates. Also runs automatically every `LUM_CHECK_INTERVAL_MINUTES`. |
| | **☰** menu | *Theme:* System / Light / Dark (stored in the browser) · *Configuration:* **Settings** (see [Configuration](configuration.md#in-the-web-ui)), **Export …** / **Import …** (see [Export and import](configuration.md#export-and-import)) · *Actions:* **Run auto-update now**, **Send test notification** (see [Notifications](configuration.md#notifications-telegram)) · *Account:* **Change password** (not shown without login). |
| Status line | | Last check, backup mode, guests hidden by the `no-lum` tag (hover for their IDs), the next automatic update, the cluster nodes, result of **Refresh list**, and a yellow note when the host script is outdated (in a cluster: on which node). |
| Above the list | **All** · **With updates** · **Security** | Filter the list: every guest, only guests with OS or app updates, only guests with security updates (stored in the browser). Bulk selections only include visible guests. |
| | **Select all with updates** · **OS update** · **App update** | Update several guests in one go – see [Updating several guests](#updating-several-guests). |
| | **Auto-update …** | Update the selected guests automatically in the maintenance window – see [Automatic updates](#automatic-updates-maintenance-window). |
| Each row | ☐ `ID` · `LXC`/`VM` · node | Checkbox for a bulk update (the one in the header selects all running guests). Container or VM; in a [cluster](installation.md#proxmox-cluster) also its node. VMs need the QEMU guest agent, otherwise **no guest agent** is shown. |
| | `▸ N packages` · `N security` | Pending OS updates – click to list them. **security**: how many come from a security repository (see [Security updates](#security-updates)); they are marked in the list. |
| | App column | Installed app version (green) or `installed → latest` (orange) with a link to the release page, PyPI or npm. **held back**: the community script pins this version; **pre-release**: the installed version is newer than the latest stable one. Without a version source: *updated with the OS packages*, *no version check (Docker)* or *Version unknown* – see [App updates](#app-updates). |
| | **Check** | Checks this one guest. |
| | **OS update** | Opens the update dialog (backup and cleanup checkboxes), then runs apt/apk with a live log. |
| | **App update** | Community-script app update. Highlighted when a newer app version exists. Not shown for VMs, containers tagged `self-created`, apps *updated with the OS packages* and apps with *no update via the script*. |
| | `low disk: … free` | Less free space in `/` than an update needs – see [Free space](#free-space-before-an-update). |
| | `restart required` · **Restart** | After updates the guest should be restarted – hover the badge for the reason, see [Restart after updates](#restart-after-updates). |
| | **Backups** | The guest's LUM snapshots (**Rollback**, **Delete**) and vzdump backups (**Restore**, **Delete**) – see [Backup, cleanup and rollback](#backup-cleanup-and-rollback). |
| Queue | **Log** · **Cancel remaining** · **Clear** | Progress of a bulk update, see below. |
| History | `ID` · Name | Guest of the entry; the name stays visible after the guest was removed. |
| | **Rollback** · **Delete** | Roll back to / delete the snapshot made before that update. With vzdump: **Delete** removes the backup made before that update (not protected ones). |
| | **Log** · **Remove** | Show the stored log / remove the entry (the snapshot or backup is kept). |
| | **Clear history** | Removes all finished entries (running updates stay). Two optional checkboxes also delete **all snapshots** and/or **all vzdump backups** LUM made, for every guest it manages – see [Backup, cleanup and rollback](#backup-cleanup-and-rollback). |

Below 1100 px window width every row turns into a card. The footer shows the version.
If the host script on the Proxmox host is older than this LUM version needs, a yellow
note below the status line says so and shows the command to update it.

## Updating several guests

Tick the checkboxes of the guests (or **Select all with updates**: every running guest
with OS or app updates), then click **OS update** or **App update** above the list. The
update dialog asks once for the backup (and for OS updates the cleanup) checkbox; it
applies to all of them.

- The guests are updated **one after the other**, each exactly like a single update –
  with its own backup, cleanup, live log and history entry. A queued guest shows
  **queued** in its row.
- The **Queue** section shows each guest's state (*waiting*, *running*, *succeeded*,
  *failed*, *skipped*, *cancelled*) and the reason, and **Log** opens the live log.
- Guests with nothing to do are **skipped**: OS update – no pending packages, not
  running; app update – VMs, apps that come with the OS packages or are already up to
  date. Apps without a version check (e.g. Docker) are updated. An app update the
  community script refuses (exit 75/113/114) counts as skipped as well.
- A failed update does **not** stop the queue; the next guest is updated anyway.
- **Cancel remaining** cancels the waiting guests; the one being updated finishes.
  When the queue is done, **Clear** removes it (a new bulk update replaces it as well).
- The queue lives in memory: restarting LUM ends it after the running update.
  Settings can't be saved while it runs.

## Automatic updates (maintenance window)

1. Set the **maintenance window** in ☰ → **Settings** → *Auto-update*: the days
   (`sun`, `sat,sun`, `mon-fri`, `daily`), the start (`03:00`) and optionally an end
   (`05:00`; an end before the start means the next day). The time is the local time of
   the LUM container.
2. Select the guests and click **Auto-update …** above the list: **Off**, **OS
   updates** or **OS and app updates**. The guests show `auto: OS` / `auto: OS + app`,
   the status line the next window.

When the window starts, LUM checks all guests and then updates the auto-update guests
through the [queue](#updating-several-guests): first the OS updates, then the app
updates – each with backup, free space check, cleanup and log as usual, marked
**auto** in the history. Guests with nothing to do are skipped.

- **End:** guests that have not started when the window ends are cancelled; the update
  running then finishes. Without an end everything is updated.
- **Restart guests that need it afterwards** (setting): guests that show *restart
  required* after their updates are restarted at the end – never LUM's own container.
- **Run auto-update now** (☰ menu) runs the same without waiting for the window, e.g.
  to try the setup.
- LUM must be running at the start time (it checks every 20 seconds and starts a window
  up to 5 minutes late). A missed window is not caught up.

## Security updates

A package counts as a security update when apt lists it from a `*-security` suite
(Debian `trixie-security`, Ubuntu `noble-security`, also when Ubuntu lists it as
`noble-updates,noble-security`). The tile **Security updates** sums them up over all
running guests, the filter **Security** shows only the guests that have some.

- An OS update always installs *all* pending packages, security ones included.
- Alpine (apk) has no separate security channel, so Alpine guests never show security
  updates – their updates may still contain security fixes.

## App updates

Containers created with the community scripts have an `update` command. LUM runs it in
the official silent mode (`PHS_SILENT=1`, the same call the community scripts'
`tools/pve/update-apps.sh` makes) and compares the installed with the latest app version.

### Where the version comes from

LUM reads the app's `ct/<app>.sh` from the community scripts and uses the same source the
script itself checks:

| The script uses | Latest version from | Installed version from |
|---|---|---|
| `check_for_gh_release` | highest stable GitHub release | `~/.<app>` in the container |
| `check_for_codeberg_release` | highest stable Codeberg release | `~/.<app>` |
| `check_for_gl_release` | highest stable GitLab release (also self-hosted GitLab) | `~/.<app>` |
| `check_for_gh_tag` | newest GitHub tag | `~/.<app>` |
| no check, but deploys the app's release (`fetch_and_deploy_*_release`) | as above | `~/.<app>` |
| no check, `pip install … --upgrade` | PyPI | `pip show` in the container |
| no check, `npm … -g` | npm registry | `npm ls -g` in the container |

Like the community scripts LUM takes the *highest* stable release (no drafts or
pre-releases, only tags with the script's prefix, if any). When a script **pins** a
version on purpose (e.g. Immich: "each release is tested individually"), LUM shows that
version as the latest and marks the app **held back** – the tooltip shows the reason.
An installed pre-release that is newer than the latest stable release (e.g. `0.45.0a1`
vs. `0.44.0`) is not an update – LUM shows it green with a **pre-release** hint.

Apps without a usable source show a hint instead of a version, and can still be updated:

- **updated with the OS packages** – the app comes from apt/apk (e.g. Zammad); its
  updates show up as OS updates. The **App update** button is hidden for these: the
  community script's update would only run the OS update as well.
- **no update via the script** – the community script's update only prints a hint and
  changes nothing (e.g. UniFi OS Server, Whisparr: *"The app offers a built-in
  updater"*; Cosmos updates itself; Frigate needs a new container). Update these in the
  app itself. The tooltip shows the script's message, the **App update** button is
  hidden, and if such an update runs anyway (API, bulk) it counts as *skipped*, not
  *succeeded*.
- **no version check (Docker)** – e.g. Home Assistant.
- **Version unknown** – e.g. apps downloaded directly from the vendor.

The GitHub API allows 60 requests per hour without a token; LUM caches each version for an
hour. With many containers set `LUM_GITHUB_TOKEN` (menu ☰ → **Settings**).

In silent mode the community script deliberately stops in these cases:

| Exit | Meaning | Fix |
|---|---|---|
| 75 | The update needs interactive mode | Run `update` manually inside the container |
| 113 | The container has less CPU/RAM than the script requires | Increase resources (e.g. Tandoor: 4 CPU / 4 GB) |
| 114 | `/boot` is more than 80 % full | Free up space |

## Free space before an update

A package manager that runs out of space halfway leaves a broken system, and a vzdump
that fills its storage fails. So before every update LUM checks – and does not start
the update (no backup either) if there is too little:

- **in the guest:** free space in `/` must be at least `LUM_MIN_FREE_MB` (default
  500 MB; menu ☰ → **Settings**, `0` turns the check off). Guests below that show
  **low disk** in the list already after a check.
- **on the vzdump storage** (only with `LUM_BACKUP_MODE=vzdump` and the backup ticked):
  the free space must hold the backup – estimated from the size of the guest's last LUM
  backup, without one from its data (about 60 % after compression), plus 10 %. Proxmox
  Backup Server is not checked: it deduplicates, a backup needs little new space.

The log shows the numbers (`### Free space in /: 1302 MB …`). If the space can't be
read (e.g. host script older than 8), the log says so and the update runs anyway.

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
- The **Backups** button of a guest lists its LUM snapshots and its LUM vzdump backups on
  all active backup storages (dir, NFS, CIFS, PBS), newest first.
- **Restore** (vzdump) shuts the guest down, restores it from the backup and starts it
  again if it was running. Containers keep their root disk storage and privilege level,
  VM disks go back to their original storages. Everything since the backup is lost and
  Proxmox deletes the guest's snapshots – the history marks them as removed. The restore
  runs as a job with a live log and shows up in the history as **Restore**.
- **Delete** (vzdump) removes the backup from the storage. Backups **protected** in
  Proxmox are shown but can't be deleted; remove the protection in Proxmox first.
- Backups made by your own backup jobs never appear and can't be restored or deleted by LUM.
- **Clear history** can delete all of LUM's snapshots and/or vzdump backups at once
  (checkboxes in its dialog), for every container and VM LUM manages. Only LUM's own
  ones (`lum_*`, note `lxc-update-manager`); protected backups and guests with a running
  job are skipped. It runs in the background, the panel bottom right shows the progress
  and the result (hover it for skipped or failed items).
- Deleting a snapshot or backup can take a while. Proxmox reports no percentage, so the
  button and a panel bottom right show the elapsed time until it is done.

<img alt="Backups dialog with LUM snapshots and vzdump backups" src="images/backups.png" width="70%">

## Cleanup after an OS update

With the checkbox **Clean up afterwards** (default: `LUM_CLEANUP=true`, menu ☰ →
**Settings**) an OS update ends with:

- apt: `apt-get autoremove` (packages nothing needs any more, e.g. old kernels in a VM)
  and `apt-get clean` (the downloaded `.deb` files);
- apk: `apk cache clean` (only does something when a package cache is set up).

The log shows how much space it freed (`--- cleanup freed 312 MB`). A failed cleanup
only warns – the update itself has succeeded by then. Untick it to keep the downloaded
packages or to look at what autoremove would remove first. App updates don't clean up.

## Restart after updates

An update replaces libraries and programs on disk, but running services keep using the
old version until they are restarted – a fixed OpenSSL only helps once nginx has been
restarted. After every check LUM looks for:

- **services still using replaced files** – processes that map a deleted (= replaced)
  library or binary, the same test `needrestart` makes (nothing needs to be installed in
  the guest);
- **the reboot flag** `/var/run/reboot-required` that some packages set;
- **a newer kernel** (VMs only – containers share the host's kernel): the newest
  `/boot/vmlinuz-*` is not the running one.

Then the guest shows **restart required** (the tooltip lists the reason and the
services) and a **Restart** button. It reboots the container or VM (`pct reboot` /
`qm reboot`), waits until it answers again, checks it and shows up in the history as
**Restart**. The badge is gone as soon as the restart is done. A restart is never done
automatically.

**LUM's own container** (found by its host name, marked **LUM** in the list) can be
restarted the same way: the host reboots it a few seconds after the job is stored, LUM
comes back with it and the page reloads. LUM does not roll back or restore its own
container – that would stop LUM halfway; use the Proxmox UI for it. If LUM was stopped
while a job ran, the history entry is closed as *Interrupted* when LUM starts again.

Restarts outside LUM count too: every minute LUM reads the guest list from the host
(status and uptime, no commands in the guests). A guest whose uptime dropped – rebooted
in the Proxmox UI, from inside, or by a host reboot – or that was started loses the
badge and is checked again within about a minute. The same look-up keeps the
running/stopped status current and adds new guests.

Needs host script 7 or newer; with an older one nothing is shown.

## VMs

OS updates, bulk updates, checks, security updates, snapshots, vzdump backups, rollback,
restore, the free space check, cleanup and *restart required* (including a newer kernel)
work for VMs too. Proxmox can only run commands inside a VM through the **QEMU guest
agent**, so each VM needs:

1. the agent installed and running in the VM, e.g. Debian/Ubuntu:
   `apt install qemu-guest-agent && systemctl enable --now qemu-guest-agent`
2. **QEMU Guest Agent** enabled in Proxmox (VM → Options), then a full VM shutdown and start
   (a reboot from inside the VM is not enough).

Without it the VM shows **no guest agent** (the tooltip explains what's missing).
Supported are Linux VMs with apt or apk. The agent returns the output only when a command
has finished, so the log of a VM update appears at the end. App updates are LXC only.

## Excluding containers and VMs

Give a container or VM the Proxmox tag **`no-lum`** and LUM leaves it alone: it is not
listed, not checked and not updated. The host script refuses every command for such a
guest as well, so not even a misbehaving LUM could touch it. The status line shows how
many guests are hidden (hover for their IDs). The tag is case-insensitive.

Add the tag in the Proxmox UI (guest → *Summary* → pencil next to the tags) or on the
host:

```bash
pct set <CTID> --tags "no-lum"   # container
qm set <VMID> --tags "no-lum"    # VM
```

`--tags` replaces all tags of the guest. To keep existing ones, list them too, separated
by `;` (e.g. `--tags "mytag;no-lum"`).

The change shows up with the next **Refresh list** or **Check all**. Remove the tag to let
LUM manage the guest again.

### Containers without app updates: `self-created`

A container you built yourself may still have an `update` command (e.g. copied from a
community-script container) and then shows up as a community-script app. Give it the tag
**`self-created`** and LUM manages only its OS updates:

- no **App update** button, no app version check, no app column;
- bulk app updates skip it (*tagged self-created*), the API refuses an app update.

```bash
pct set <CTID> --tags "self-created"
```

The list picks the tag up within a minute (or with **Refresh list**); the tag is
case-insensitive. Unlike `no-lum` this is not enforced by the host script – it is only
about what LUM shows and offers.

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
