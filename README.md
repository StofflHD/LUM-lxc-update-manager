# LUM – LXC Update Manager

Manage OS updates (apt/apk) of all LXC containers **and VMs** and the application
updates of containers on a Proxmox host from one web UI. Built for containers created
with the [Proxmox VE Community Scripts](https://github.com/community-scripts/ProxmoxVE).

What changed in which version: [CHANGELOG.md](CHANGELOG.md).

**Contents:** [Architecture](#architecture) · [Features](#features) ·
[Using the web UI](#using-the-web-ui) · [App updates](#app-updates) · [VMs](#vms) ·
[Backup, cleanup and rollback](#backup-cleanup-and-rollback) · [Login](#login) ·
[Installation](#installation-easy-installer) · [Updating](#updating) ·
[Reverse proxy](#reverse-proxy) · [Configuration](#configuration) ·
[Troubleshooting](#troubleshooting) · [API](#api)

## Architecture

```
LXC "lxc-update-manager" (FastAPI + SQLite + web UI, port 8080)
        │  SSH, restricted key (forced command)
        ▼
Proxmox host: /usr/local/bin/lxc-update-wrapper
        │  pct exec / qm guest exec / snapshot / vzdump / pvesh
        ▼
LXC 101, 102 …   VM 200, 201 … (via QEMU guest agent)
```

On the host the manager can **only** run the verbs of the wrapper script (`version`,
`list`, `info`, `check`, `upgrade`, `app-version`, `app-update`, `snapshot`, `snapshots`,
`prune-snapshots`, `delete-snapshot`, `rollback`, `backup`, `prune-backups`). It never gets
a shell, and it can only touch its own snapshots and backups.

## Features

- LXC containers and QEMU VMs in one list, with tags (templates are left out)
- Detects the package manager (apt / apk) and community-script containers (`/usr/bin/update`)
- Checks for pending OS updates on a schedule and on demand; new or removed
  containers/VMs show up with **Refresh list** without a full check
- OS update with one click and a live log (VMs: log at the end)
- App versions of community-script containers: installed vs. latest GitHub release
- App update through the community scripts' `update` command in silent mode
- Snapshot or vzdump backup before every update (can be skipped per update), automatic
  cleanup, rollback with one click, deleting snapshots
- History with stored logs; single entries or the whole history can be removed
- Login (one admin account), CSRF protection, lockout after failed logins
- Responsive web UI without horizontal scrollbars, light and dark theme
- `update` command inside the LUM container, warning when the host script is outdated
- Easy installer for the Proxmox host (install, update, uninstall)

## Using the web UI

| Where | Control | What it does |
|---|---|---|
| Top right | **Refresh list** | Re-reads only the list of containers/VMs (fast). New running guests are checked right away; the result is shown below the status line. |
| | **Check all** | Re-reads the list and checks every running guest for OS and app updates. Also runs automatically every `LUM_CHECK_INTERVAL_MINUTES`. |
| | **☰** menu | **Theme** (System / Light / Dark, stored in the browser) and **Change password**. |
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
  see [`FORWARDED_ALLOW_IPS`](#reverse-proxy)).
- Protection against other websites (CSRF): API calls that change something need the
  header `X-Requested-With: lum`, WebSockets check the origin.
- The connection itself is plain **HTTP** on port 8080. For access from outside your home
  network use a [reverse proxy](#reverse-proxy) with HTTPS.

## Installation (easy installer)

The installer runs on the **Proxmox host** and does everything: creates a Debian LXC
(1 CPU, 512 MB RAM, 4 GB), installs the app and the host script, adds the restricted SSH
key, stores the host keys, sets up the login, tests the connection and starts the service.

On the Proxmox host (shell as root):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh)
```

Answer a few questions (Enter accepts the suggestion): container ID, hostname, storage,
bridge, IP, backup mode, web UI login and – optionally – a root password for the container
(leave it empty for none; you get a root shell with `pct enter <CTID>` on the host and can
set one later with `pct exec <CTID> -- passwd`). At the end you get the URL and the login.

| Option | Effect |
|---|---|
| *(none)* | New installation |
| `--update` | Install the newest version **and** the newest host script, settings and data are kept |
| `--uninstall` | Remove SSH access and host script, optionally delete the container |
| `-y` | No questions, use the suggestions or preset `LUM_*` variables |

Unattended, e.g. with vzdump to a PBS:

```bash
LUM_CTID=150 LUM_BACKUP_MODE=vzdump LUM_BACKUP_STORAGE=pbs \
  bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) -y
```

All variables are listed at the top of [install.sh](install.sh). The installation log is
`/var/log/lxc-update-manager-install.log`.

From a local copy instead of GitHub: copy the project to the host and run
`bash install.sh` in it. Manual installation inside an existing Debian LXC:
`bash deploy/install.sh`, then follow the steps it prints.

## Updating

Inside the LUM container (e.g. `pct enter <CTID>`), like the community scripts:

```bash
update
```

`update --check` only shows whether a new version exists, `update --force` reinstalls
(useful right after a release, GitHub may serve the old version number for a few minutes).
Settings, login, SSH key and history are kept; the database is copied to
`data/lum.db.before-update` first.

The container cannot update the host script on the Proxmox host – on purpose, it may only
run its fixed verbs there. When a new version needs a newer host script, `update` and the
web UI tell you so. Then run on the Proxmox host (updates both):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update
```

## Reverse proxy

LUM can run behind any reverse proxy for HTTPS and access from outside. Requirements:

- **Own (sub)domain**, e.g. `lum.example.com`. A sub-path like `example.com/lum/` is **not**
  supported (the UI uses absolute paths).
- **WebSockets** must be passed through (live update logs, path `/ws/…`).
- Pass the public host name: either keep the `Host` header, or send `X-Forwarded-Host`.
  Otherwise the live log is rejected by the origin check.
- Send `X-Forwarded-For` and `X-Forwarded-Proto`, and tell LUM to trust the proxy:
  add its IP to `/opt/lxc-update-manager/.env` and restart the service:

  ```bash
  pct exec <CTID> -- bash -c 'echo FORWARDED_ALLOW_IPS=192.168.1.20 >> /opt/lxc-update-manager/.env && systemctl restart lxc-update-manager'
  ```

  Then the login lockout counts per real client (without it, 5 wrong passwords from
  anyone would lock out everybody behind the proxy), and the session cookie is marked
  `Secure` automatically when the proxy speaks HTTPS.
- Long VM updates send no output for a while; LUM pings the WebSocket every 20 s, so the
  default proxy timeouts are fine.
- Optional: restrict port 8080 of the LUM container (e.g. Proxmox firewall) to the proxy.
- Already a login in front (Authelia, Authentik, …)? Then `LUM_AUTH_DISABLED=true` turns
  off LUM's own login – only if the proxy really protects every path.

### Nginx Proxy Manager

*Hosts → Proxy Hosts → Add Proxy Host*

- **Domain Names:** `lum.example.com`
- **Scheme:** `http`, **Forward Hostname / IP:** IP of the LUM container, **Port:** `8080`
- **Websockets Support:** on
- *SSL* tab: request a certificate, **Force SSL** on

NPM sends `Host`, `X-Forwarded-For` and `X-Forwarded-Proto` by default. Set
`FORWARDED_ALLOW_IPS` to the IP of the NPM container/host.

### nginx

```nginx
map $http_upgrade $connection_upgrade { default upgrade; '' close; }

server {
    listen 443 ssl;
    server_name lum.example.com;
    ssl_certificate     /etc/ssl/lum.example.com/fullchain.pem;
    ssl_certificate_key /etc/ssl/lum.example.com/privkey.pem;

    location / {
        proxy_pass http://192.168.1.50:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
```

### Caddy

```caddy
lum.example.com {
    reverse_proxy 192.168.1.50:8080
}
```

Caddy gets the certificate, passes WebSockets and sets the forwarding headers by itself.

### Traefik (file provider)

```yaml
http:
  routers:
    lum:
      rule: Host(`lum.example.com`)
      entryPoints: [websecure]
      service: lum
      tls:
        certResolver: letsencrypt
  services:
    lum:
      loadBalancer:
        servers:
          - url: http://192.168.1.50:8080
```

Traefik passes WebSockets and the forwarding headers by default.

## Configuration

Settings live in `/opt/lxc-update-manager/.env` inside the LUM container (written by the
installer, all options with comments in [.env.example](.env.example)). After a change:
`systemctl restart lxc-update-manager`.

| Option | Default | Meaning |
|---|---|---|
| `LUM_PVE_HOST` | – | IP of the Proxmox host (SSH target) |
| `LUM_PVE_PORT` / `LUM_PVE_USER` | `22` / `root` | SSH port and user on the host |
| `LUM_SSH_KEY_PATH` / `LUM_KNOWN_HOSTS_PATH` | `data/id_ed25519` / `data/known_hosts` | SSH key and pinned host keys |
| `LUM_DB_PATH` | `data/lum.db` | Database (containers, history) |
| `LUM_CHECK_INTERVAL_MINUTES` | `360` | Automatic **Check all** interval |
| `LUM_MAX_PARALLEL_CHECKS` | `4` | Guests checked at the same time |
| `LUM_GITHUB_TOKEN` | – | Optional, raises the GitHub API limit for app versions from 60 to 5000 requests/h |
| `LUM_BACKUP_MODE` | `snapshot` | `snapshot`, `vzdump` or `none` |
| `LUM_SNAPSHOT_KEEP` | `2` | LUM snapshots kept per guest |
| `LUM_BACKUP_STORAGE` | – | vzdump target storage (required for `vzdump`) |
| `LUM_BACKUP_VZDUMP_MODE` | `snapshot` | vzdump mode: `snapshot`, `suspend` or `stop` |
| `LUM_BACKUP_KEEP` | `2` | LUM vzdump backups kept per guest |
| `LUM_AUTH_FILE` | `data/auth.json` | Login (set with `python -m app.passwd`) |
| `LUM_SESSION_HOURS` | `12` | Session lifetime |
| `LUM_AUTH_DISABLED` | `false` | Turn off the login (only behind an authenticating proxy) |
| `LUM_COOKIE_SECURE` | `false` | Force the `Secure` cookie flag (automatic behind a trusted HTTPS proxy) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Reverse proxy IP(s) whose forwarding headers are trusted |
| `LUM_DEMO` | `false` | Simulated guests for development |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Yellow note *host script is outdated* | Run the installer with `--update` on the Proxmox host. |
| VM shows **no guest agent** | Install `qemu-guest-agent` in the VM, enable *QEMU Guest Agent* in its options, fully stop and start the VM. |
| Update fails at the snapshot step | The guest's storage has no snapshot support – use `LUM_BACKUP_MODE=vzdump`. |
| App update *skipped* (exit 75/113/114) | See [App updates](#app-updates). |
| Live log stays empty behind a proxy | WebSockets not passed through, or the public host name is lost – see [Reverse proxy](#reverse-proxy). |
| Everyone is locked out after wrong passwords behind a proxy | Set `FORWARDED_ALLOW_IPS` to the proxy IP. |
| `update` says *up to date* right after a release | GitHub caches for a few minutes – wait or use `update --force`. |
| Forgot the password | `pct exec <CTID> -- bash -c 'cd /opt/lxc-update-manager && venv/bin/python -m app.passwd'` |

Logs: `pct exec <CTID> -- journalctl -u lxc-update-manager -n 100`; installer log on the
host: `/var/log/lxc-update-manager-install.log`. Test the host script directly on the
host (without SSH): `SSH_ORIGINAL_COMMAND="list" /usr/local/bin/lxc-update-wrapper`.

## Local development (demo mode, no Proxmox)

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
LUM_DEMO=true .venv/bin/uvicorn app.main:app --reload --port 8080
```

Demo mode simulates containers and VMs (the app version lookup still queries GitHub) and
creates the login `admin` / `demo`.

## API

All endpoints except `/api/login` and `/api/auth/state` need the session cookie; requests that change something need
the header `X-Requested-With: lum`.

| Method | Path | Description |
|---|---|---|
| POST | `/api/login` · `/api/logout` | Log in / out (cookie) |
| GET | `/api/auth/state` · `/api/me` | Login configured? / current user |
| POST | `/api/password` | Change password |
| GET | `/api/status` | Version, last check, backup mode, host script version |
| GET | `/api/containers` | All containers/VMs with update status |
| POST | `/api/sync` | Re-read the list of containers/VMs only (no package checks) |
| POST | `/api/refresh` | Check all (async) |
| POST | `/api/containers/{id}/check` | Check one guest |
| POST | `/api/containers/{id}/update?kind=os\|app&backup=true\|false` | Start an update → job |
| GET | `/api/jobs/{id}` | Job status + log |
| WS | `/ws/jobs/{id}` | Live log of a job |
| GET | `/api/containers/{id}/snapshots` | The guest's LUM snapshots (`lum_*`) |
| DELETE | `/api/containers/{id}/snapshots/{name}` | Delete one LUM snapshot |
| POST | `/api/containers/{id}/rollback?snapshot=lum_…` | Start a rollback → job |
| GET | `/api/history` · `/api/history/{id}/log` | History / stored log |
| DELETE | `/api/history/{id}` · `/api/history` | Remove one / all finished entries (snapshots are kept) |

## Roadmap

- [x] App version detection (installed vs. latest upstream version)
- [x] Non-interactive app updates for community scripts
- [x] VM support via the QEMU guest agent
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
