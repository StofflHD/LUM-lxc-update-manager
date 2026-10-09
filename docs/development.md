# Development

[← Back to the README](../README.md)

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
LUM_DEMO=true .venv/bin/uvicorn app.main:app --reload --port 8080
```

Demo mode needs no Proxmox: it simulates the host and creates the login `admin` / `demo`.
The app version lookup is real (community scripts, GitHub, Codeberg, PyPI), so it needs
internet access. The simulated guests cover the main cases:

| ID | Guest | Shows |
|---|---|---|
| 101 | homeassistant | community script, *no version check (Docker)* |
| 102 | adguard | *updated with the OS packages* |
| 103 / 104 | tandoor / vaultwarden | GitHub app versions; 104 (apk) is *skipped* on an app update (under-provisioned) and has *low disk* (an OS update stops) |
| 105 | test-debian | stopped container |
| 106 | forgejo | Codeberg app version |
| 107 | motioneye | PyPI app version, *restart required* (a service uses replaced libraries) |
| 108 | backup-server | tagged `no-lum` – hidden |
| 200 / 201 | debian-vm / windows-vm | VM with guest agent (*restart required*: newer kernel) / without (*no guest agent*) |

Updates, snapshots, vzdump backups (103 and 200 start with some, one of them protected),
rollback, restore and cleanup are simulated in memory (reset on restart); deletes take a
few seconds like on a real host. The database is real (`LUM_DB_PATH`). For vzdump instead
of snapshots set `LUM_BACKUP_MODE=vzdump` and `LUM_BACKUP_STORAGE=pbs`.
