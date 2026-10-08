# Changelog

All notable changes to LUM are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

Update an installation with `update` inside the LUM container. When an entry says
**host script**, also run the installer on the Proxmox host:
`bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update`

## [1.4.1] – 2026-10-08

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

## [1.4.0] – 2026-10-08

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

## [1.3.0] – 2026-10-08

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

## [1.2.0] – 2026-10-08

### Changed
- The web UI scales with the window instead of using a fixed maximum width
  (only very wide screens are capped at 2200 px for readability).
- No horizontal scrollbars: when space gets short the action buttons wrap inside their
  cell, and below 1100 px (small laptops, tablets, phones) every table row turns into a card with the
  column names above the values.

## [1.1.6] – 2026-10-08

### Changed
- The page is wider (up to 1600 px instead of 1200 px), so the tables fit without a
  horizontal scrollbar on common screens.
- Long `owner/repo` names in the App column are shortened with `…`; the full name is
  shown as a tooltip.

## [1.1.5] – 2026-10-08

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

## [1.1.4] – 2026-10-08

### Fixed
- Cleaner update logs: progress output that rewrites a single line (dpkg's
  `Reading database ... 5%`) now shows only its final state instead of one line per step.
- No more locale warnings (`perl: warning: Setting locale failed`) in update logs:
  commands in the containers run with `C.UTF-8` instead of the host's language. **Host script**
- OS updates no longer let `apt-listchanges` read package changelogs. **Host script**

## [1.1.3] – 2026-10-08

### Added
- **Delete** button next to **Rollback** in the history, so snapshots can be deleted
  right where they are listed. Before, deleting was only possible in the snapshot
  dialog behind the small ⟲ button.

### Changed
- The ⟲ button of a container is now labelled **Snapshots**.

## [1.1.2] – 2026-10-08

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

## [1.1.1] – 2026-10-08

### Fixed
- After an update the browser could keep running the old web UI (for example without
  the snapshot **Delete** button), because the UI files were sent without cache
  headers. They are now revalidated on every load.
- When the host script is too old for an action, the error now says so and shows the
  command to update it, instead of a bare `verb not allowed`.

### Added
- This changelog.

## [1.1.0] – 2026-10-08

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

## [1.0.0] – 2026-10-08

First release.

- Web UI for OS updates (apt/apk) and app updates of community-script containers,
  with live logs and history.
- App version detection: installed version vs. latest GitHub release.
- App updates in the community scripts' silent mode (`PHS_SILENT=1`).
- Snapshot or vzdump backup before every update, automatic cleanup, rollback.
- Restricted SSH access to the host through a forced-command host script.
- Login with a scrypt-hashed password, signed session cookies and rate limiting.
- Easy installer for the Proxmox host, installs straight from GitHub.

[1.4.1]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/f6db5c7...main
[1.4.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/0b1859e...f6db5c7
[1.3.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/942b4bf...0b1859e
[1.2.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/5ec7124...942b4bf
[1.1.6]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/30069ba...5ec7124
[1.1.5]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/8df17f1...30069ba
[1.1.4]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/8f8c7b6...8df17f1
[1.1.3]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/c1a6caa...deb7d69
[1.1.2]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/321a53e...c1a6caa
[1.1.1]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/5745b96...321a53e
[1.1.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/8a43c04...5745b96
[1.0.0]: https://github.com/StofflHD/LUM-lxc-update-manager/commit/8a43c04
