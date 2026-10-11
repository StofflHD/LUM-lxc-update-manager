# Configuration

[← Back to the README](../README.md)

Settings live in `/opt/lxc-update-manager/.env` inside the LUM container (written by the
installer, all options with comments in [.env.example](../.env.example)).

## In the web UI

Menu **☰** → **Settings** edits the most common options: check interval, parallel checks,
GitHub token, free space and cleanup, all backup options, the auto-update window, the
Telegram notifications, history and log rotation, session lifetime, Secure cookie and the reverse proxy IP(s). LUM validates the input, writes `.env` (comments and other lines are kept) and
restarts itself; the page reloads when LUM is back. Saving is refused while an update is
running.

- The connection to the Proxmox host (IP, port, user) is shown but **not** editable there:
  a wrong value would lock LUM out, and the SSH host key is pinned to the IP. Use the
  installer with `--update` or edit `.env`.
- Paths, `LUM_AUTH_DISABLED` and `LUM_DEMO` are not shown – turning off the login with
  a click would be too easy. Edit `.env` for those.
- The GitHub and Telegram tokens are never sent back to the browser: leave the field
  empty to keep it, enter `-` to remove it.

## Display

The group *Display* at the top of ☰ → **Settings** is about this browser only – stored
there, not in `.env`. A choice applies at once, without **Save** (also while an update
runs); changing the language reloads the page and asks first if other settings in the
dialog were changed but not saved yet:

- **Theme:** *System* (default, follows the operating system), *Light* or *Dark*.
- **Language** of the web UI: English or German. Without a choice LUM follows the
  browser's language. Error messages from the server follow it too; update logs and the
  server log stay English.
- **Date and time format:** *System* (default) follows the browser / operating system,
  or a fixed format – `31.12.2026 23:59`, `31/12/2026 23:59`, `12/31/2026 11:59 PM` or
  `2026-12-31 23:59`. It applies to numbers (`3,8 GB` / `3.8 GB`) too.

The Telegram messages have their own language setting: *Notifications (Telegram)* →
*Language of the messages* (`LUM_NOTIFY_LANGUAGE`). More languages: see
[Translating](translating.md).

## Notifications (Telegram)

1. In Telegram open **@BotFather**, send `/newbot` and follow it – you get the **bot
   token** (`123456789:AAH…`).
2. Send your new bot any message (for a group: add the bot to the group and write there).
3. Open `https://api.telegram.org/bot<token>/getUpdates` in a browser; the **chat id**
   is at `"chat":{"id":…}` (a group's id is negative).
4. Enter both in ☰ → **Settings** → *Notifications (Telegram)* and save; then ☰ →
   **Send test notification**.

What is reported:

| Setting | Message |
|---|---|
| **Report updates** `failures` (default) | a single update, rollback, restore or restart that failed; after a bulk or automatic update one summary if anything failed |
| **Report updates** `all` | every one of them, and a summary after every bulk / automatic update |
| **Report available updates after the scheduled check** | the guests with pending packages (security ones counted), app updates and *restart required*, and the Proxmox hosts with pending packages or *reboot required* – only when that differs from the last message |

LUM only sends to Telegram (`api.telegram.org`, HTTPS); it never reads messages, so the
bot can't be used to control LUM.

## History and log rotation

LUM tidies up its history by itself – right after a start and then every hour (☰ →
**Settings** → *History & logs*; `0` turns a limit off):

| Setting | Default | What happens |
|---|---|---|
| **Keep history entries for … days** | `365` | finished entries that are older are removed |
| **Keep at most … history entries** | `1000` | beyond the newest ones, older finished entries are removed |
| **Keep the update logs for … days** | `90` | the log of older entries is removed; the entry (time, guest, result, backup) stays, **Log** then says so |
| **Server log: lines kept in memory** | `2000` | lines in ☰ → **Server log**; older ones are in `journalctl` |

Running updates are never touched. Rotation only removes history entries and logs – the
snapshots and vzdump backups stay (under the **Backups** button of the guest). The
history in the web UI shows the newest 50 entries; the export contains all that are
kept. Imported entries are rotated like the others.

## Export and import

☰ → **Export …** downloads a JSON file, ☰ → **Import …** reads it back – into the same
LUM (e.g. after a reinstall) or another one. You choose what goes in and what comes back:

| Part | Export | Import |
|---|---|---|
| **Settings** | the options of the settings dialog; the GitHub / Telegram tokens only if you tick *with the tokens* (then keep the file safe) | validated like in the settings dialog, written to `.env`, LUM restarts |
| **Auto-update** | the mode of every container / VM | only for guests with the **same ID and name** here; the others are listed as skipped |
| **History** | all finished entries with their logs | entries that are not there yet (same guest, type and start) are added – importing twice adds nothing |

Not included: the connection to the Proxmox host (IP, port, user, SSH key, host key) and
the login – they belong to the installation. Importing settings is refused while an
update is running.

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
| `LUM_CHECK_INTERVAL_MINUTES` | `360` | Automatic **Check all** interval, counted from the last check (also a manual one, and across restarts of LUM) |
| `LUM_MAX_PARALLEL_CHECKS` | `4` | Guests checked at the same time |
| `LUM_CLEANUP` | `true` | Default of **Clean up afterwards** in the update dialog: `apt autoremove` + `apt clean` (apk: `apk cache clean`) after an OS update (see [Cleanup](usage.md#cleanup-after-an-os-update)) |
| `LUM_AUTO_DAYS` | – | Days of the maintenance window: `sun`, `sat,sun`, `mon-fri`, `daily`; empty = no automatic updates (see [Automatic updates](usage.md#automatic-updates-maintenance-window)) |
| `LUM_AUTO_TIME` | `03:00` | Start of the window (local time of the LUM container) |
| `LUM_AUTO_UNTIL` | – | Optional end; guests not started by then are cancelled |
| `LUM_AUTO_RESTART` | `false` | After the updates, restart guests that need it (not LUM's own container) |
| `LUM_TELEGRAM_TOKEN` | – | Bot token from @BotFather (see [Notifications](#notifications-telegram)) |
| `LUM_TELEGRAM_CHAT_ID` | – | Chat id of you or a group (negative), or `@channelname` |
| `LUM_NOTIFY` | `failures` | Report updates: `failures`, `all` or `off` |
| `LUM_NOTIFY_UPDATES` | `true` | Report available updates after the scheduled check |
| `LUM_NOTIFY_LANGUAGE` | `en` | Language of the Telegram messages: `en`, `de` (a file in `app/static/lang`, see [Translating](translating.md)) |
| `LUM_HISTORY_DAYS` | `365` | Finished history entries older than this are removed; `0` = keep (see [rotation](#history-and-log-rotation)) |
| `LUM_HISTORY_MAX` | `1000` | Keep at most this many finished history entries (the newest); `0` = no limit |
| `LUM_LOG_DAYS` | `90` | Update logs older than this are removed, the entry stays; `0` = keep |
| `LUM_SERVER_LOG_LINES` | `2000` | Lines of the **Server log** in the web UI (100–50000, in memory) |
| `LUM_MIN_FREE_MB` | `500` | Free space in the guest's `/` needed for an update; `0` turns the check off (see [Free space](usage.md#free-space-before-an-update)) |
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
