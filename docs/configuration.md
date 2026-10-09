# Configuration

[← Back to the README](../README.md)

Settings live in `/opt/lxc-update-manager/.env` inside the LUM container (written by the
installer, all options with comments in [.env.example](../.env.example)).

## In the web UI

Menu **☰** → **Settings** edits the most common options: check interval, parallel checks,
GitHub token, all backup options, session lifetime, Secure cookie and the reverse proxy
IP(s). LUM validates the input, writes `.env` (comments and other lines are kept) and
restarts itself; the page reloads when LUM is back. Saving is refused while an update is
running.

- The connection to the Proxmox host (IP, port, user) is shown but **not** editable there:
  a wrong value would lock LUM out, and the SSH host key is pinned to the IP. Use the
  installer with `--update` or edit `.env`.
- Paths, `LUM_AUTH_DISABLED` and `LUM_DEMO` are not shown – turning off the login with
  a click would be too easy. Edit `.env` for those.
- The GitHub token is never sent back to the browser: leave the field empty to keep it,
  enter `-` to remove it.

## By hand

`pct exec <CTID> -- nano /opt/lxc-update-manager/.env`, then
`pct exec <CTID> -- systemctl restart lxc-update-manager`.

## All options

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
| `LUM_SECRET_FILE` | `data/secret.key` | Key that signs the session cookies (created automatically) |
| `LUM_SESSION_HOURS` | `12` | Session lifetime |
| `LUM_AUTH_DISABLED` | `false` | Turn off the login (only behind an authenticating proxy) |
| `LUM_COOKIE_SECURE` | `false` | Force the `Secure` cookie flag (automatic behind a trusted HTTPS proxy) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Reverse proxy IP(s) whose forwarding headers are trusted |
| `LUM_DEMO` | `false` | Simulated guests for development |
