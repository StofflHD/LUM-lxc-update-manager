# Changelog

All notable changes to LUM are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

Update an installation with `update` inside the LUM container. When an entry says
**host script**, also run the installer on the Proxmox host:
`bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update`

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

[1.1.1]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/5745b96...main
[1.1.0]: https://github.com/StofflHD/LUM-lxc-update-manager/compare/8a43c04...5745b96
[1.0.0]: https://github.com/StofflHD/LUM-lxc-update-manager/commit/8a43c04
