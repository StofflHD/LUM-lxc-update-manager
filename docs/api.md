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
