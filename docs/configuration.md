# Configuration

[← Back to the README](../README.md)

Settings live in `/opt/lxc-update-manager/.env` inside the LUM container (written by the
installer, all options with comments in [.env.example](../.env.example)). After a change:
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
