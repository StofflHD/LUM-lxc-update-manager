# API

[← Back to the README](../README.md)

All endpoints except `/api/login` and `/api/auth/state` need the session cookie; requests that change something need
the header `X-Requested-With: lum`.

| Method | Path | Description |
|---|---|---|
| POST | `/api/login` · `/api/logout` | Log in / out (cookie) |
| GET | `/api/auth/state` · `/api/me` | Login configured? / current user |
| POST | `/api/password` | Change password |
| GET · POST | `/api/settings` | Editable settings / save them (LUM restarts) |
| GET | `/api/status` | Version, last and next check (`last_refresh`, `next_refresh`), backup mode, host script version, hidden (`no-lum`) guests, `queue`, `nodes` (cluster nodes with their host script version; `host_script.nodes_outdated`), `cleanup` (default of the cleanup checkbox), `purge` (progress of deleting all snapshots / backups), `maintenance` (window, next start, running, last run) |
| GET | `/api/containers` | All containers/VMs with update status (`upgradable`, `security` = the security updates among them, `restart_required`, `restart_reboot`, `restart_services`, `disk_free_kb`, `disk_size_kb`, `low_disk`, `self` = LUM's own container, `self_created` = tagged `self-created`, `auto_update` = `os`/`all`/null, `node`) |
| POST | `/api/sync` | Re-read the list of containers/VMs only (no package checks) |
| POST | `/api/refresh` | Check all (async) |
| POST | `/api/containers/{id}/check` | Check one guest |
| POST | `/api/containers/{id}/update?kind=os\|app&backup=true\|false&cleanup=true\|false` | Start an update → job (`cleanup` left out: `LUM_CLEANUP`) |
| GET | `/api/containers/{id}/release-notes` | Releases between the installed and the latest app version: `releases` (`tag`, `name`, `body`, `published`, `url`, `cut`), `more` |
| POST | `/api/containers/{id}/restart` | Reboot the guest → job |
| GET | `/api/jobs/{id}` | Job status + log |
| POST | `/api/auto` | Body `{"vmids": [101], "mode": "off"\|"os"\|"all"}` – auto-update per guest |
| POST | `/api/maintenance/run` | Run the automatic updates now |
| GET | `/api/export?settings=true&secrets=false&guests=true&history=true` | Export as JSON (download) |
| POST | `/api/import` | Body `{"data": <export>, "settings": true, "guests": true, "history": true}` – returns what was imported; LUM restarts after settings |
| POST | `/api/notify/test` | Send a Telegram test message (502 with Telegram's error) |
| GET | `/api/queue` | Bulk update queue: `vmid`, `kind`, `backup`, `state` (`waiting`, `running`, `ok`, `failed`, `skipped`, `cancelled`), `job_id`, `note` |
| POST | `/api/queue` | Body `{"vmids": [101, 102], "kind": "os"\|"app", "backup": true, "cleanup": true}` – update them one after the other |
| DELETE | `/api/queue` | Cancel the waiting guests, or clear a finished queue |
| WS | `/ws/jobs/{id}` | Live log of a job |
| GET | `/api/containers/{id}/snapshots` | The guest's LUM snapshots (`lum_*`) |
| DELETE | `/api/containers/{id}/snapshots/{name}` | Delete one LUM snapshot |
| POST | `/api/containers/{id}/rollback?snapshot=lum_…` | Start a rollback → job |
| GET | `/api/containers/{id}/backups` | The guest's LUM vzdump backups: `id` (= ctime), `volid`, `storage`, `ctime`, `size`, `protected` |
| DELETE | `/api/containers/{id}/backups/{backup_id}` | Delete one LUM vzdump backup (not protected ones) |
| POST | `/api/containers/{id}/restore?backup={backup_id}` | Restore the guest from a LUM vzdump backup → job |
| GET | `/api/logs?level=INFO\|WARNING\|ERROR` | LUM's log since its start (last 2000 lines, no access log) |
| GET | `/api/history` · `/api/history/{id}/log` | History (with the guest `name`, `auto` = started by the maintenance window) / stored log |
| DELETE | `/api/history/{id}` · `/api/history` | Remove one / all finished entries (snapshots and backups are kept) |
| DELETE | `/api/history?snapshots=true&backups=true` | Remove all finished entries and also delete all of LUM's snapshots and/or vzdump backups (background; progress in `/api/status` → `purge`) |
| DELETE | `/api/history/{id}/backup` | Delete the vzdump backup made before that update; `deleted: null` if it no longer exists |
