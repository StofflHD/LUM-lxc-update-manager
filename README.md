<p align="center"><img src="app/static/logo.svg" width="96" alt="LUM logo"></p>

# LUM – LXC Update Manager

Manage OS updates (apt/apk) of all LXC containers **and VMs** on a Proxmox host – and the
app updates of containers created with the
[Proxmox VE Community Scripts](https://github.com/community-scripts/ProxmoxVE) – from one
web UI. With a snapshot before every update and rollback with one click.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/dashboard-dark.png">
  <img alt="LUM web UI: containers and VMs with pending updates, app versions and the update history" src="docs/images/dashboard-light.png">
</picture>

## Features

- **Containers and VMs in one list** – LXC via `pct`, VMs via the QEMU guest agent
- **OS updates** (apt / apk) with one click and a live log
- **App updates** for community-script containers, with installed vs. latest version
- **Safety first** – snapshot or vzdump backup before every update, cleanup, rollback
- **History** with stored logs, snapshots can be rolled back or deleted from there
- **Secure by design** – the host only allows a fixed set of commands, LUM touches only
  its own snapshots and backups; login with lockout and CSRF protection
- **Responsive** web UI with light and dark theme and a settings page, works behind a reverse proxy
- **Easy installer** for the Proxmox host and an `update` command inside the container

<p>
  <img alt="Update log of an OS update" src="docs/images/update-log.png" width="64%">
  &nbsp;
  <img alt="Mobile view: every row becomes a card" src="docs/images/mobile.png" width="22%">
</p>

## Quick start

On the Proxmox host (shell as root):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh)
```

Answer a few questions (Enter accepts the suggestion). The installer creates a small
Debian container, installs LUM and the host script, and prints the URL and the login.

Update later inside the LUM container:

```bash
update
```

For VMs, install `qemu-guest-agent` in the VM and enable *QEMU Guest Agent* in its Proxmox
options – see [VMs](docs/usage.md#vms).

## Documentation

| | |
|---|---|
| [Using LUM](docs/usage.md) | Web UI, app updates, VMs, backups and rollback, login |
| [Installation and updates](docs/installation.md) | Installer options, unattended install, updating, uninstall |
| [Reverse proxy](docs/reverse-proxy.md) | HTTPS with Nginx Proxy Manager, nginx, Caddy or Traefik |
| [Configuration](docs/configuration.md) | All settings in `.env` |
| [Troubleshooting](docs/troubleshooting.md) | Common problems and fixes, logs |
| [API](docs/api.md) | HTTP API of the web UI |
| [Development](docs/development.md) | Demo mode without Proxmox |
| [Changelog](CHANGELOG.md) | What changed in which version |

## How it works

```
LXC "lxc-update-manager" (FastAPI + SQLite + web UI, port 8080)
        │  SSH, restricted key (forced command)
        ▼
Proxmox host: /usr/local/bin/lxc-update-wrapper
        │  pct exec / qm guest exec / snapshot / vzdump / pvesh
        ▼
LXC 101, 102 …   VM 200, 201 … (via QEMU guest agent)
```

LUM runs in its own container and reaches the Proxmox host over SSH with a key that may
only call the host script (`lxc-update-wrapper`). The script accepts a fixed set of verbs
(`version`, `list`, `info`, `check`, `upgrade`, `app-version`, `app-update`, `snapshot`,
`snapshots`, `prune-snapshots`, `delete-snapshot`, `rollback`, `backup`, `prune-backups`),
validates every argument and only ever touches snapshots named `lum_*` and backups with
the note `lxc-update-manager`. LUM never gets a shell on the host.

## Roadmap

- [x] App version detection and non-interactive app updates for community scripts
- [x] VM support via the QEMU guest agent
- [x] Snapshot / vzdump before updates, cleanup, rollback
- [x] Login / authentication for the web UI
- [ ] Version detection for Codeberg releases and apps without a release check
- [ ] Schedules / maintenance windows, auto-update per container
- [ ] Notifications (ntfy, Gotify, Telegram)
- [ ] Restore a vzdump backup from the web UI
- [ ] Multiple nodes / cluster

## License

Copyright (C) 2026 StofflHD

LUM is free software: you can redistribute it and/or modify it under the terms of the
[GNU General Public License version 3](LICENSE) as published by the Free Software
Foundation (GPL-3.0-only).

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU General Public License for more details.
