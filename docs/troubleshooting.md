# Troubleshooting

[← Back to the README](../README.md)

| Symptom | Cause / fix |
|---|---|
| Yellow note *host script is outdated* | Run the installer with `--update` on the Proxmox host. |
| VM shows **no guest agent** | Install `qemu-guest-agent` in the VM, enable *QEMU Guest Agent* in its options, fully stop and start the VM. |
| Update stops: *only … MB free in / of the guest* | Free up space in the guest (`apt clean`, old logs, `journalctl --vacuum-size=100M`) or enlarge its disk (Proxmox: *Resources → Disk Action → Resize*). Or lower `LUM_MIN_FREE_MB`. |
| Update stops: *only … GB free on the vzdump storage* | Free up space on the storage, or untick the backup for this one update. |
| Update fails at the snapshot step | The guest's storage has no snapshot support – use `LUM_BACKUP_MODE=vzdump`. |
| vzdump backup can't be deleted: *is protected* | The backup is protected in Proxmox (storage → Backups → Protected). Remove the protection there; LUM never does it. |
| Backups dialog shows no vzdump backups | Only backups with the note `lxc-update-manager` on an active storage are listed. Backups of your own jobs are hidden on purpose. |
| A container/VM is missing in the list | It has the Proxmox tag `no-lum` (status line: *hidden*), or it is a template. New guests appear with **Refresh list**. |
| App shows *Version unknown* or no version | The community script has no version check LUM can use – see [App updates](usage.md#where-the-version-comes-from). PyPI/npm versions need host script 4 (installer `--update` on the host). |
| App update *skipped* (exit 75/113/114) | See [App updates](usage.md#app-updates). |
| Live log stays empty behind a proxy | WebSockets not passed through, or the public host name is lost – see [Reverse proxy](reverse-proxy.md). |
| Everyone is locked out after wrong passwords behind a proxy | Set `FORWARDED_ALLOW_IPS` to the proxy IP. |
| `update` says *up to date* right after a release | GitHub caches for a few minutes – wait or use `update --force`. |
| Forgot the password | `pct exec <CTID> -- bash -c 'cd /opt/lxc-update-manager && venv/bin/python -m app.passwd'` |

Logs: `pct exec <CTID> -- journalctl -u lxc-update-manager -n 100`; installer log on the
host: `/var/log/lxc-update-manager-install.log`. Test the host script directly on the
host (without SSH): `SSH_ORIGINAL_COMMAND="list" /usr/local/bin/lxc-update-wrapper`.
