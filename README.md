# LUM – LXC Update Manager

Manage OS updates (apt/apk) and application updates of all LXC containers on a
Proxmox host from one web UI. Built for containers created with the
[Proxmox VE Community Scripts](https://github.com/community-scripts/ProxmoxVE).

What changed in which version: [CHANGELOG.md](CHANGELOG.md).

## Architecture

```
LXC "lxc-update-manager" (FastAPI + SQLite + web UI)
        │  SSH, restricted key (forced command)
        ▼
Proxmox host: /usr/local/bin/lxc-update-wrapper
        │  pct exec / pct snapshot / vzdump / pvesh
        ▼
LXC 101, 102, 103 …
```

On the host the manager can **only** run the verbs of the wrapper script (`list`, `info`,
`check`, `upgrade`, `app-version`, `app-update`, `snapshot`, `snapshots`,
`prune-snapshots`, `delete-snapshot`, `rollback`, `backup`, `prune-backups`). It never gets a shell.

## Features

- Discovers containers automatically (`pvesh`), including tags
- Detects the package manager (apt / apk) and community-script containers (`/usr/bin/update`)
- Checks for pending OS updates, on a schedule and on demand
- OS update with one click, with a live log over WebSocket
- App versions: installed (`~/.<app>` inside the container) vs. latest GitHub release
  (taken from `check_for_gh_release` in the matching `ct/<app>.sh`)
- App update through the community-scripts `update` command in the official silent mode
  (`PHS_SILENT=1`, the same call the official `tools/pve/update-apps.sh` makes)
- Backup before every update: snapshot, vzdump backup or none (`LUM_BACKUP_MODE`),
  can be turned off per update with a checkbox in the update dialog
- Automatic cleanup of old backups after a successful update
- Rollback to a snapshot with one click (history or the **Snapshots** button of a container)
- Delete the manager's snapshots from the web UI (history or the **Snapshots** button)
- History with stored logs
- Login for the web UI (one admin account)

### App updates: skipped updates

In silent mode the community script deliberately stops in these cases:

| Exit | Meaning | Fix |
|---|---|---|
| 75 | The update needs interactive mode | Run `update` manually inside the container |
| 113 | The container has less CPU/RAM than the script requires | Increase resources (e.g. Tandoor: 4 CPU / 4 GB) |
| 114 | `/boot` is more than 80 % full | Free up space |

Apps without `check_for_gh_release` (e.g. AdGuard, Home Assistant) show no version but
can still be updated.

## Backup, cleanup and rollback

| `LUM_BACKUP_MODE` | What happens before the update | Cleanup |
|---|---|---|
| `snapshot` (default) | `pct snapshot` named `lum_<date>_<time>` | keeps `LUM_SNAPSHOT_KEEP` (default 2) per container |
| `vzdump` | Backup to `LUM_BACKUP_STORAGE` (dir, NFS, CIFS or PBS), note `lxc-update-manager` | keeps `LUM_BACKUP_KEEP` (default 2) per container |
| `none` | nothing | – |

- If the backup fails, the update does **not** run.
- The update dialog has a checkbox to skip the backup for that one update. Without a
  backup the update cannot be rolled back.
- Cleanup only runs after a successful (or deliberately skipped) update. After a failure
  every backup is kept.
- The manager **only** touches its own backups: snapshots named `lum_*` and backups with
  the note `lxc-update-manager`. The host script enforces this, not just the app.
  Protected backups are never deleted.
- Snapshots need storage with snapshot support (LVM-thin, ZFS, Ceph). On plain directory
  storage use `vzdump`.
- **Rollback** shuts the container down, rolls it back and starts it again if it was
  running. On **ZFS** this only works for the newest snapshot: Proxmox refuses the
  rollback while newer snapshots exist.
- Snapshots can also be deleted by hand: **Delete** in the history, or the
  **Snapshots** button of a container → **Delete**.
- A vzdump backup is restored in the Proxmox UI (container → Backup → Restore), not in
  the manager.

## Login

The web UI has one admin account. The installer asks for username and password
(leave the password empty to get a random one, shown at the end).

- Change the password: **Password** at the top right of the web UI, or inside the
  container `cd /opt/lxc-update-manager && venv/bin/python -m app.passwd [username]`
  (also if you forgot it). Takes effect immediately and logs out all sessions.
- The password is stored scrypt-hashed in `data/auth.json`, never in plain text.
- Session: signed cookie (HttpOnly, SameSite=Strict), valid for `LUM_SESSION_HOURS` (12 h).
- After 5 failed attempts the IP is locked for 15 minutes.
- Protection against other websites (CSRF): API calls that change something need the
  header `X-Requested-With: lum`, WebSockets check the origin.
- The connection itself is **HTTP**. For access from outside your home network put an
  HTTPS reverse proxy in front and set `LUM_COOKIE_SECURE=true`.
- If a reverse proxy with its own login already sits in front (Authelia, Authentik …),
  the login can be turned off with `LUM_AUTH_DISABLED=true`.

## Installation (easy installer)

The installer runs on the **Proxmox host** and does everything: creates a Debian LXC
(1 CPU, 512 MB RAM, 4 GB), installs the app and the host script, adds the restricted SSH
key, stores the host keys, sets up the login, tests the connection and starts the service.

The container's root password is optional: leave it empty and the container has none,
you get a root shell with `pct enter <CTID>` on the host. Set one later with
`pct exec <CTID> -- passwd`.

### Straight from GitHub (recommended)

On the Proxmox host (shell as root):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh)
```

Answer a few questions (Enter accepts the suggestion); at the end you get the URL and
the login. The installer downloads the code from GitHub and removes the download again.

### Updating

Inside the LUM container (e.g. `pct enter <CTID>`), like the community scripts:

```bash
update
```

`update --check` only shows whether a new version exists, `update --force` reinstalls.
Settings, login, SSH key and history are kept; the database is copied to
`data/lum.db.before-update` first.

The container cannot update the host script on the Proxmox host – on purpose, it may only
run its fixed verbs there. When a new version needs a newer host script, `update` and the
web UI tell you so. Then run on the Proxmox host (updates both):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update
```

### From a local copy

```bash
scp -r lxc-update-manager root@<PROXMOX-IP>:/root/
bash /root/lxc-update-manager/install.sh
```

| Option | Effect |
|---|---|
| *(none)* | New installation |
| `--update` | Install the new version, settings and data are kept |
| `--uninstall` | Remove SSH access and host script, optionally delete the container |
| `-y` | No questions, use the suggestions or preset `LUM_*` variables |

Unattended, e.g. with vzdump to a PBS:

```bash
LUM_CTID=150 LUM_BACKUP_MODE=vzdump LUM_BACKUP_STORAGE=pbs bash install.sh -y
```

The installation log is `/var/log/lxc-update-manager-install.log`.
To change settings later: `pct exec <CTID> -- nano /opt/lxc-update-manager/.env`,
then `pct exec <CTID> -- systemctl restart lxc-update-manager`. All options are
documented in [.env.example](.env.example).

### Manual installation

Alternatively run `bash deploy/install.sh` inside an existing Debian LXC and follow the
steps it prints.

Test the wrapper on the host (without SSH):

```bash
SSH_ORIGINAL_COMMAND="list" /usr/local/bin/lxc-update-wrapper
SSH_ORIGINAL_COMMAND="check 101" /usr/local/bin/lxc-update-wrapper
```

## Local development (demo mode, no Proxmox)

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
LUM_DEMO=true .venv/bin/uvicorn app.main:app --reload --port 8080
```

Demo mode simulates the containers (the app version lookup still queries GitHub) and
creates the login `admin` / `demo`.

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/login` · `/api/logout` | Log in / out (cookie) |
| POST | `/api/password` | Change password |
| GET | `/api/containers` | All containers with update status |
| POST | `/api/refresh` | Check all containers (async) |
| POST | `/api/containers/{id}/check` | Check one container |
| POST | `/api/containers/{id}/update?kind=os\|app&backup=true\|false` | Start an update → job |
| GET | `/api/jobs/{id}` | Job status + log |
| WS | `/ws/jobs/{id}` | Live log of a job |
| GET | `/api/containers/{id}/snapshots` | The manager's snapshots (`lum_*`) |
| DELETE | `/api/containers/{id}/snapshots/{name}` | Delete one of the manager's snapshots |
| POST | `/api/containers/{id}/rollback?snapshot=lum_…` | Start a rollback → job |
| GET | `/api/history` | History |
| GET | `/api/history/{id}/log` | Stored log |

## Roadmap

- [x] App version detection (installed vs. latest upstream version)
- [x] Non-interactive app updates for community scripts
- [ ] Version detection for Codeberg releases and apps without a release check
- [ ] Schedules / maintenance windows, auto-update per container
- [ ] Notifications (ntfy, Gotify, Telegram)
- [x] Rollback to a snapshot with one click, clean up old snapshots
- [x] vzdump backup as an alternative to snapshots
- [ ] Restore a vzdump backup from the web UI
- [x] Login / authentication for the web UI
- [ ] Multiple nodes / cluster

## License

Copyright (C) 2026 StofflHD

LUM is free software: you can redistribute it and/or modify it under the terms of the
[GNU General Public License version 3](LICENSE) as published by the Free Software
Foundation (GPL-3.0-only).

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU General Public License for more details.
