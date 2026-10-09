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
| 103 / 104 | tandoor / vaultwarden | GitHub app versions; 104 (apk) is *skipped* on an app update (under-provisioned) |
| 105 | test-debian | stopped container |
| 106 | forgejo | Codeberg app version |
| 107 | motioneye | PyPI app version |
| 108 | backup-server | tagged `no-lum` – hidden |
| 200 / 201 | debian-vm / windows-vm | VM with guest agent / without (*no guest agent*) |

Updates, snapshots, rollback and cleanup are simulated in memory (reset on restart), the
database is real (`LUM_DB_PATH`).
