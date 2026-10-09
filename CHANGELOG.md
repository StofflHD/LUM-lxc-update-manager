# Changelog

All notable changes to LUM are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). While the version starts with 0, LUM is in active
development: anything may still change between versions. Versions stay below 1.0 until the
first final release, which will be 1.0.0.

Update an installation with `update` inside the LUM container. When an entry says
**host script**, also run the installer on the Proxmox host:
`bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update`

## [0.17.2] – 2026-10-09

### Fixed
- An app update of e.g. UniFi OS Server showed *succeeded* although the community
  script only printed *"The app offers a built-in updater. Please use it."* and changed
  nothing. LUM now recognises the 13 community scripts whose update does nothing but
  print such a hint (built-in updater, updates itself, no update function, new
  container needed): the app shows *no update via the script* with the message in the
  tooltip, and there is no **App update** button. If such an update runs anyway – or a
  script says so only at runtime, e.g. GLPI, WordPress – it ends as *skipped*.
- Terminal control codes (clear screen, colours: `[H[J`, `[1;92m` …) of the community
  scripts no longer end up in the logs.

## [0.17.1] – 2026-10-09

### Changed
- No **App update** button for apps *updated with the OS packages*: their community
  script update only runs the OS update, which the **OS update** button does already.
  (Bulk app updates skipped them before; the API still allows it.)

## [0.17.0] – 2026-10-09

### Added
- Proxmox tag **`self-created`**: for containers you built yourself that LUM would take
  for a community-script app. LUM then manages only their OS updates – no **App update**
  button, no app version check, skipped by bulk app updates, refused by the API.
- API: `/api/containers` has `self_created`.

## [0.16.0] – 2026-10-09

### Added
- **Clear history** has two options: also delete **all snapshots** and/or **all vzdump
  backups** LUM made, for every container and VM it manages. As always only LUM's own
  ones; protected backups and guests with a running job are skipped. Runs in the
  background with progress and a result in the panel bottom right (skipped / failed
  items in its tooltip).
- API: `DELETE /api/history?snapshots=true&backups=true`; `/api/status` → `purge`.

## [0.15.2] – 2026-10-09

**Host script** (version 10) – run the installer with `--update` on the Proxmox host.

### Fixed
- Restarting LUM's own container from the web UI left the history entry on *running*
  for good (LUM stopped in the middle of the job) and it couldn't be removed. LUM now
  recognises its own container (by host name, marked **LUM** in the list): the job is
  stored first and the host reboots the container 5 seconds later, detached from the
  SSH session; the page reloads when LUM is back.
- Entries still *running* when LUM starts are closed: LUM's own restart as succeeded,
  anything else as *Interrupted*. This also fixes the entry left by 0.15.1.
- LUM refuses to roll back or restore its own container (it would stop halfway and
  could leave the container stopped) and points to the Proxmox UI.

### Added
- Host script: `restart <vmid> background`. API: `/api/containers` has `self`.

## [0.15.1] – 2026-10-09

### Fixed
- *restart required* stayed visible long after a restart: it was only re-evaluated by
  the next full check, which right after a boot could fail (then it stayed until the
  next scheduled check, up to hours later). Now **Restart** clears it as soon as the
  guest is back, the restart check runs before `apt-get update`, and a check that fails
  because the guest is still booting is retried.

### Added
- Every minute LUM reads the guest list from the host (status and uptime only). Guests
  restarted outside LUM (Proxmox UI, inside the guest, host reboot) lose *restart
  required* and are checked again; status changes and new guests show up without
  **Refresh list**.

## [0.15.0] – 2026-10-09

**Host script** (version 9) – run the installer with `--update` on the Proxmox host.

### Added
- **Cleanup after an OS update** is a checkbox in the update dialog (also for bulk
  updates): `apt-get autoremove` + `apt-get clean`, on Alpine `apk cache clean`. The
  log shows how much space it freed. Default from the new setting `LUM_CLEANUP`
  (true, editable in the settings).
- API: `cleanup` for `/api/containers/{id}/update` and `POST /api/queue`.

### Changed
- `apt-get autoremove` used to run after every OS update; now it can be turned off. A
  failed cleanup only warns instead of failing the update.
- Settings: new group *Updates* (free space, cleanup).

### Documentation
- Usage: sections in a logical order (bulk and security updates first, then app updates,
  free space, backups, cleanup, restart, VMs); what works for VMs; troubleshooting for
  *restart required*, skipped queue entries and Alpine.

### Fixed
- The update dialog said to restore a vzdump backup in the Proxmox UI – that works in
  LUM's Backups dialog since 0.10.0.

## [0.14.0] – 2026-10-09

**Host script** (version 8) – run the installer with `--update` on the Proxmox host.

### Added
- **Free space check before every update:** the guest's `/` needs at least
  `LUM_MIN_FREE_MB` (default 500 MB, editable in the settings, `0` = off), and with
  vzdump the backup storage must hold the backup (estimated from the last LUM backup;
  not checked on Proxmox Backup Server). Too little space stops the update before the
  backup, with a clear message; the log shows the numbers.
- *low disk* badge in the list for guests below `LUM_MIN_FREE_MB` (updated by every check).
- Host script verb `space`; API: `/api/containers` has `disk_free_kb`, `disk_size_kb`
  and `low_disk`.

## [0.13.0] – 2026-10-09

**Host script** (version 7) – run the installer with `--update` on the Proxmox host.

### Added
- **Restart required:** after every check LUM looks for services that still use
  replaced libraries or programs (like `needrestart`, nothing to install in the guest),
  the reboot flag `/var/run/reboot-required` and – for VMs – a newer installed kernel.
  The guest then shows a *restart required* badge (the tooltip says why and lists the
  services) and a **Restart** button that reboots it, waits until it answers again and
  logs it in the history.
- Host script verbs `restart-needed` and `restart`.
- API: `POST /api/containers/{id}/restart`; `/api/containers` has `restart_required`,
  `restart_reboot` and `restart_services`.

## [0.12.0] – 2026-10-09

### Added
- **Security updates** are highlighted: a red `N security` badge next to the pending
  packages, the packages are marked in the list, and a new tile **Security updates**
  counts them over all running guests. A package is a security update when apt lists it
  from a `*-security` suite (Debian and Ubuntu); apk has no such channel.
- **Filter** above the list: **All**, **With updates**, **Security** (remembered in the
  browser). Bulk selections ("select all") only take the visible guests.
- API: `/api/containers` lists the security updates per guest (`security`).

## [0.11.0] – 2026-10-09

### Added
- **Update several guests in one go:** checkboxes in the guest list, **Select all with
  updates**, and **OS update** / **App update** above the list. The guests are updated
  one after the other, each with its own backup, log and history entry.
- **Queue** section with the state of every guest (waiting, running, succeeded, failed,
  skipped, cancelled), the reason and the live log; **Cancel remaining** stops the
  guests that haven't started yet. Guests with nothing to do are skipped, a failed
  update doesn't stop the others.
- API: `GET`/`POST`/`DELETE /api/queue`; `/api/status` contains the queue.


### Added
- The history shows the name of the container or VM next to its ID (also in the log
  title). The name is stored with each entry, so it stays visible after the guest was
  removed; existing entries get the name of guests that still exist.

### Documentation
- New screenshots (Backups button, name column in the history) and one of the Backups
  dialog; usage: history name column, delete progress; configuration: `LUM_SECRET_FILE`;
  development: what the demo simulates for backups and how to try vzdump.

## [0.10.2] – 2026-10-09

### Changed
- Deleting a snapshot or vzdump backup shows its progress: a spinner with the elapsed
  seconds on the button and a panel bottom right (*Deleting … 12 s*, then *✔ Deleted*).
  Proxmox reports no percentage for this, so the time shows that it is still running.

### Fixed
- Deleting from the history looked like nothing happened: the list is refreshed every
  5 s and the busy button came back as a normal **Delete** button while the delete was
  still running.
- Closing the Backups dialog during a delete no longer reopens it when the delete ends.

## [0.10.1] – 2026-10-09

### Added
- vzdump backups can be deleted from the history too: **Delete** next to an update with a
  vzdump backup removes the backup made before it (matched by storage and time, so older
  entries work as well). Protected backups are refused; a backup that was already removed
  in Proxmox just marks the entry as deleted.
- API: `DELETE /api/history/{id}/backup`.

## [0.10.0] – 2026-10-09

**Host script** (version 6) – run the installer with `--update` on the Proxmox host.

### Added
- **Restore** a guest from one of LUM's vzdump backups in the web UI. The guest is shut
  down, restored and started again if it was running; containers keep their root disk
  storage and privilege level. Runs as a job with a live log and a **Restore** entry in
  the history; the snapshots Proxmox drops on a restore are marked as removed.
- **Delete** LUM's vzdump backups in the web UI. Backups protected in Proxmox are shown
  but never deleted.
- Host script verbs `backups`, `delete-backup` and `restore-backup` – again limited to
  backups with the note `lxc-update-manager` and refused for guests tagged `no-lum`.
- API: `GET`/`DELETE /api/containers/{id}/backups[/{backup_id}]`,
  `POST /api/containers/{id}/restore?backup=…`.

### Changed
- The **Snapshots** button of a guest is now **Backups** and lists snapshots and vzdump
  backups.

## [0.9.1] – 2026-10-09

### Fixed
- An installed pre-release (e.g. motionEye 0.45.0a1) was offered a "downgrade" to the
  latest stable release (0.44.0); the app update then did nothing but reported success.
  Versions are compared properly now – pre-releases rank below the final release with
  the same number – and an app newer than the latest stable release is shown green with
  a *pre-release* hint instead of an update.
- The highest release of a forge is picked with the same rule (rc below final).

### Documentation
- Usage: status line and app column (version sources, *held back*, *pre-release*) in the
  web UI overview; development: what the demo mode simulates; reverse proxy: *Cache
  Assets* in Nginx Proxy Manager is fine; README: `pkg-version` and `no-lum` in "How it
  works".

## [0.9.0] – 2026-10-09

### Added
- Proxmox tag **`no-lum`**: containers and VMs with it are not listed, checked or updated.
  The host script refuses every command for them too (exit 6). The status line shows how
  many guests are hidden. **Host script**

## [0.8.0] – 2026-10-09

### Added
- App versions from more sources, read from the app's community script: **Codeberg**,
  **GitLab** (also self-hosted, e.g. `GITLAB_URL`), **GitHub tags**, apps that deploy a
  release without a separate check, and apps updated with **pip** (PyPI) or **npm**.
  **Host script** (new verb `pkg-version` for pip/npm)
- Versions **pinned** by a script (e.g. Immich) are shown as the latest and the app is
  marked *held back*, with the script's reason as tooltip – no more false update hints.
- Apps without a version source say why: *updated with the OS packages*, *no version
  check (Docker)* or *Version unknown*.

### Changed
- The latest version is the highest stable release (no drafts / pre-releases, tag prefix
  respected), the same rule the community scripts use – not just GitHub's "latest".
- Scripts that check several components (e.g. authentik: geoipupdate, xmlsec, authentik)
  now use the check that matches the app, not the first one.
- The release link points to the right forge or registry.

## [0.7.0] – 2026-10-09

### Added
- **Settings** in the menu: edit check interval, parallel checks, GitHub token, backup
  options, session lifetime, Secure cookie and reverse proxy IP(s) in the web UI. LUM
  validates the input, writes `.env` (keeping comments and other lines) and restarts
  itself. Not possible while an update is running.
- The Proxmox connection is shown read-only; paths, `LUM_AUTH_DISABLED` and the demo
  mode are not exposed. The GitHub token is never sent back to the browser.

## [0.6.1] – 2026-10-09

### Fixed
- After an update a browser or reverse proxy cache (e.g. *Cache Assets* in Nginx Proxy
  Manager) could serve the old `style.css` with the new page – the logo then stuck to the
  title. CSS, JS and icon links now carry the version (`?v=0.6.1`), so every update uses
  new URLs.
- A bit more space between logo and title.

## [0.6.0] – 2026-10-09

### Added
- Logo (container cube with an update arrow on a blue tile) in the header, on the login
  page and in the README.
- Favicon (SVG, PNG fallback and `/favicon.ico`) and an Apple touch icon for home-screen
  shortcuts.

## [0.5.2] – 2026-10-09

### Changed
- The README is short now, with screenshots; the details moved to [docs/](docs/)
  (usage, installation, reverse proxy, configuration, troubleshooting, API, development).
- The log dialog title names the guest, the type and the time (`CT 101 – OS update · …`)
  instead of `History #4`.
- Demo mode produces realistic package names, apt/apk output and app versions.

### Fixed
- With the login turned off, an empty user name left a gap before the header buttons.

## [0.5.1] – 2026-10-08

### Fixed
- Behind a reverse proxy the login lockout counted the proxy's IP, so 5 wrong passwords
  from anyone locked out everybody. The service now reads `.env` as environment, so
  `FORWARDED_ALLOW_IPS=<proxy IP>` makes it count per real client.
- Behind a proxy that replaces the `Host` header the live log (WebSocket) was rejected;
  `X-Forwarded-Host` is accepted now.
- The session cookie is marked `Secure` automatically when a trusted proxy reports HTTPS.

### Added
- README: using the web UI, reverse proxy guide (Nginx Proxy Manager, nginx, Caddy,
  Traefik), all configuration options, troubleshooting, complete API list.

## [0.5.0] – 2026-10-08

### Added
- **Refresh list** button: re-reads only the list of containers and VMs from the host, so
  new or removed guests show up right away without checking all packages. New running
  guests are checked in the background; the result (new / removed IDs) is shown below
  the status line.
- Menu **☰** at the top right.
- Light mode: **Theme** in the menu switches between *System* (follows the operating
  system, as before), *Light* and *Dark*. The choice is stored in the browser and also
  used on the login page. Status colors in the light theme are darker for readability.

### Changed
- **Change password** moved into the menu.

## [0.4.0] – 2026-10-08

### Added
- **VM support:** QEMU VMs appear next to the containers (marked `VM`, templates are left
  out). OS updates (apt/apk), checks, snapshots, vzdump backups, cleanup and rollback work
  for VMs through the QEMU guest agent. **Host script**
- VMs without a reachable guest agent show **no guest agent** with instructions in the
  tooltip.
- App updates (community scripts) stay LXC only; VMs don't show an **App update** button.

### Changed
- The first summary tile shows containers / VMs; dialogs and the history say `CT 101`
  or `VM 200`.

### Notes
- A VM needs `qemu-guest-agent` installed and **QEMU Guest Agent** enabled in its Proxmox
  options. The agent returns output only at the end, so VM update logs appear when the
  update has finished.

## [0.3.0] – 2026-10-08

### Changed
- The web UI scales with the window instead of using a fixed maximum width
  (only very wide screens are capped at 2200 px for readability).
- No horizontal scrollbars: when space gets short the action buttons wrap inside their
  cell, and below 1100 px (small laptops, tablets, phones) every table row turns into a card with the
  column names above the values.

## [0.2.6] – 2026-10-08

### Changed
- The page is wider (up to 1600 px instead of 1200 px), so the tables fit without a
  horizontal scrollbar on common screens.
- Long `owner/repo` names in the App column are shortened with `…`; the full name is
  shown as a tooltip.

## [0.2.5] – 2026-10-08

### Added
- The history can be cleaned up in the web UI: **Remove** on a single entry, or
  **Clear history** for all finished entries. Running updates stay; snapshots and
  backups are not deleted and remain available under the container's **Snapshots**
  button.

### Fixed
- Badges like `up to date` or `2 packages` wrapped onto two lines in narrow columns and
  looked broken; they now always stay on one line.
- The disclosure triangle of the package list sat on its own line above the badge.
- The action buttons column was laid out as a flex box instead of a table cell, so its
  row lines didn't match the rest of the table.

## [0.2.4] – 2026-10-08

### Fixed
- Cleaner update logs: progress output that rewrites a single line (dpkg's
  `Reading database ... 5%`) now shows only its final state instead of one line per step.
- No more locale warnings (`perl: warning: Setting locale failed`) in update logs:
  commands in the containers run with `C.UTF-8` instead of the host's language. **Host script**
- OS updates no longer let `apt-listchanges` read package changelogs. **Host script**

## [0.2.3] – 2026-10-08

### Added
- **Delete** button next to **Rollback** in the history, so snapshots can be deleted
  right where they are listed. Before, deleting was only possible in the snapshot
  dialog behind the small ⟲ button.

### Changed
- The ⟲ button of a container is now labelled **Snapshots**.

## [0.2.2] – 2026-10-08

### Fixed
- Deleting (and rolling back) snapshots could do nothing at all: the browser's own
  confirm box can be switched off by the browser ("prevent this page from creating
  additional dialogs") and then answers *Cancel* without showing anything. LUM now uses
  its own confirmation dialog everywhere, and the result of a delete (or its error) is
  shown directly in the snapshot dialog.
- Failed snapshot deletions are logged on the server
  (`journalctl -u lxc-update-manager`).

### Changed
- The version number moved from the status line to a new footer with copyright,
  license, GitHub and changelog links.

## [0.2.1] – 2026-10-08

### Fixed
- After an update the browser could keep running the old web UI (for example without
  the snapshot **Delete** button), because the UI files were sent without cache
  headers. They are now revalidated on every load.
- When the host script is too old for an action, the error now says so and shows the
  command to update it, instead of a bare `verb not allowed`.

### Added
- This changelog.

## [0.2.0] – 2026-10-08

### Added
- `update` command inside the LUM container: updates LUM from GitHub, keeps settings,
  login, key and history, backs up the database first. `--check` and `--force`.
- Version number (`VERSION`), shown in the web UI.
- The host script reports its version; `update` and the web UI warn when it is
  outdated and show the command for the host. **Host script**
- Checkbox in the update dialog to skip the snapshot/vzdump for a single update.
- Delete the manager's own snapshots from the web UI (⟲ button → **Delete**).
  **Host script**
- Installer: optional root password for the container (set via `chpasswd`, never on
  the command line); `LUM_ROOT_PASSWORD` for unattended installs.
- `curl` is installed in the container.

### Changed
- The update confirmation is a dialog instead of a browser prompt.
- The LUM container itself is no longer listed as a community-script app.

## [0.1.0] – 2026-10-08

First release.

- Web UI for OS updates (apt/apk) and app updates of community-script containers,
  with live logs and history.
- App version detection: installed version vs. latest GitHub release.
- App updates in the community scripts' silent mode (`PHS_SILENT=1`).
- Snapshot or vzdump backup before every update, automatic cleanup, rollback.
- Restricted SSH access to the host through a forced-command host script.
- Login with a scrypt-hashed password, signed session cookies and rate limiting.
- Easy installer for the Proxmox host, installs straight from GitHub.

[0.17.2]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/77ad4ba...main
[0.17.1]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/a5c78e2...77ad4ba
[0.17.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/ad0a71b...a5c78e2
[0.16.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/420daa7...ad0a71b
[0.15.2]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/43d796e...420daa7
[0.15.1]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/7b33fe8...43d796e
[0.15.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/0058f2b...7b33fe8
[0.14.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/7b02fc6...0058f2b
[0.13.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/0dee04b...7b02fc6
[0.12.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/dd5e36c...0dee04b
[0.11.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/7fd71e4...dd5e36c
[0.10.3]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/ef91f34...7fd71e4
[0.10.2]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/9a0f48d...ef91f34
[0.10.1]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/47427f2...9a0f48d
[0.10.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/6e2f468...47427f2
[0.9.1]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/e347e7a...6e2f468
[0.9.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/58559b4...e347e7a
[0.8.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/777d354...58559b4
[0.7.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/ddbc9a0...777d354
[0.6.1]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/4a80557...ddbc9a0
[0.6.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/d1d6bed...4a80557
[0.5.2]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/d79944e...d1d6bed
[0.5.1]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/1c46a1b...d79944e
[0.5.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/cbd7e1c...1c46a1b
[0.4.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/5218f8d...cbd7e1c
[0.3.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/fabab02...5218f8d
[0.2.6]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/18e24ac...fabab02
[0.2.5]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/2b11b8d...18e24ac
[0.2.4]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/4970284...2b11b8d
[0.2.3]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/757bac4...87d494d
[0.2.2]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/969f81f...757bac4
[0.2.1]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/93b2423...969f81f
[0.2.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/8a43c04...93b2423
[0.1.0]: https://github.com/StofflHD/LUM-lxc-update-manager/commit/8a43c04
