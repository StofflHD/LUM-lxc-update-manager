# Installation and updates

[← Back to the README](../README.md)

## Installation (easy installer)

The installer runs on the **Proxmox host** and does everything: creates a Debian LXC
(1 CPU, 512 MB RAM, 4 GB), installs the app and the host script, adds the restricted SSH
key, stores the host keys, sets up the login, tests the connection and starts the service.

On the Proxmox host (shell as root):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh)
```

Answer a few questions (Enter accepts the suggestion): container ID, hostname, storage,
bridge, IP, backup mode, web UI login and – optionally – a root password for the container
(leave it empty for none; you get a root shell with `pct enter <CTID>` on the host and can
set one later with `pct exec <CTID> -- passwd`). At the end you get the URL and the login.

| Option | Effect |
|---|---|
| *(none)* | New installation |
| `--update` | Install the newest version **and** the newest host script, settings and data are kept |
| `--uninstall` | Remove SSH access and host script, optionally delete the container |
| `-y` | No questions, use the suggestions or preset `LUM_*` variables |

Unattended, e.g. with vzdump to a PBS:

```bash
LUM_CTID=150 LUM_BACKUP_MODE=vzdump LUM_BACKUP_STORAGE=pbs \
  bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) -y
```

All variables are listed at the top of [install.sh](../install.sh). The installation log is
`/var/log/lxc-update-manager-install.log`.

From a local copy instead of GitHub: copy the project to the host and run
`bash install.sh` in it. Manual installation inside an existing Debian LXC:
`bash deploy/install.sh`, then follow the steps it prints.

## Proxmox cluster

LUM manages the guests of all nodes of a cluster from one installation. Install it on
any node as usual – that node is the one LUM talks to. The installer puts the host
script on **every online node** (over the cluster's root SSH) and prints a warning for a
node it can't reach. LUM's SSH key ends up in `/root/.ssh/authorized_keys`, which a
cluster shares in `/etc/pve`, so nothing else is needed.

How it works: `list` collects the guests of all online nodes. A command for a guest on
another node is passed on by the host script to the host script there – with the same
checks (fixed verbs, plain arguments, `no-lum`, only LUM's own snapshots and backups).
LUM itself never connects to the other nodes.

- The list shows each guest's node, the status line the number of nodes.
- A node added later, or one that was offline during the installation: run the
  installer with `--update` again. Until then LUM warns: *host script missing or outdated
  on node …*.
- Guests of an offline node are not listed while it is offline.
- A guest migrated to another node is found there automatically.

## Updating

Inside the LUM container (e.g. `pct enter <CTID>`), like the community scripts:

```bash
update
```

`update --check` only shows whether a new version exists, `update --force` reinstalls
(useful right after a release, GitHub may serve the old version number for a few minutes).
Settings, login, SSH key and history are kept; the database is copied to
`data/lum.db.before-update` first.

The container cannot update the host script on the Proxmox host – on purpose, it may only
run its fixed verbs there. When a new version needs a newer host script, `update` and the
web UI tell you so. Then run on the Proxmox host (updates both; in a cluster on the node
of the LUM container – it updates the host script on all nodes):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update
```
