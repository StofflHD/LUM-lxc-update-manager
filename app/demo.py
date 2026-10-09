"""Fake host for UI development without a Proxmox server (LUM_DEMO=true).

Containers and VMs are simulated; the app version lookup (ct script + GitHub release)
still goes to the real community-scripts repo and GitHub.
"""

import asyncio
import random
import time
from collections.abc import AsyncIterator

from .apps import AppCatalog
from .host import ContainerInfo, HostCommandError

# vmid, name, status, tags, pkg manager, ct script, installed app version, type
_CONTAINERS = [
    (101, "homeassistant", "running", "community-script;smarthome", "apt", "homeassistant", "", "lxc"),
    (102, "adguard", "running", "community-script;network", "apt", "adguard", "0.107.40", "lxc"),
    (103, "tandoor", "running", "community-script;recipes", "apt", "tandoor", "2.0.0", "lxc"),
    (104, "vaultwarden", "running", "community-script", "apk", "vaultwarden", "1.30.0", "lxc"),
    (105, "test-debian", "stopped", "", "apt", None, "", "lxc"),
    (106, "forgejo", "running", "community-script;git", "apt", "forgejo", "14.0.2", "lxc"),     # Codeberg
    (107, "motioneye", "running", "community-script;camera", "apt", "motioneye", "0.42.1", "lxc"),  # PyPI
    (108, "backup-server", "running", "no-lum", "apt", None, "", "lxc"),  # hidden from LUM
    (200, "debian-vm", "running", "", "apt", None, "", "qemu"),
    (201, "windows-vm", "running", "", "unknown", None, "", "qemu"),  # no guest agent
]
NO_AGENT = {201}

# realistic pending updates: (package, installed, new)
_PACKAGES = [
    ("libc6", "2.41-12+deb13u3", "2.41-12+deb13u4"),
    ("libc-bin", "2.41-12+deb13u3", "2.41-12+deb13u4"),
    ("openssl", "3.5.6-1~deb13u2", "3.5.7-1~deb13u3"),
    ("libssl3t64", "3.5.6-1~deb13u2", "3.5.7-1~deb13u3"),
    ("bash", "5.2.37-2+b9", "5.2.37-2+b10"),
    ("tzdata", "2026b-0+deb13u1", "2026c-0+deb13u1"),
    ("curl", "8.14.1-2+deb13u1", "8.14.1-2+deb13u2"),
    ("libcurl4t64", "8.14.1-2+deb13u1", "8.14.1-2+deb13u2"),
    ("systemd", "257.12-1~deb13u1", "257.13-1~deb13u1"),
    ("libsystemd0", "257.12-1~deb13u1", "257.13-1~deb13u1"),
    ("udev", "257.12-1~deb13u1", "257.13-1~deb13u1"),
    ("perl-base", "5.40.1-6", "5.40.1-6+deb13u1"),
    ("libsqlite3-0", "3.46.1-7+deb13u1", "3.46.1-7+deb13u2"),
    ("xz-utils", "5.8.1-1+deb13u1", "5.8.1-1+deb13u2"),
    ("liblzma5", "5.8.1-1+deb13u1", "5.8.1-1+deb13u2"),
    ("openssh-client", "1:10.0p1-7", "1:10.0p1-7+deb13u1"),
    ("libexpat1", "2.7.1-2", "2.8.3-1~deb13u1"),
    ("login", "1:4.16.0-2+really2.41-5", "1:4.16.0-2+really2.41.5-0+deb13u1"),
    ("util-linux", "2.41-5", "2.41.5-0+deb13u1"),
    ("mount", "2.41-5", "2.41.5-0+deb13u1"),
    ("libblkid1", "2.41-5", "2.41.5-0+deb13u1"),
    ("e2fsprogs", "1.47.2-3+b11", "1.47.2-3+b12"),
    ("gzip", "1.13-1", "1.13-1+deb13u1"),
    ("ca-certificates", "20250419", "20250419+deb13u1"),
]
AGENT_ERROR = (
    "error: QEMU guest agent not reachable in VM {} - install qemu-guest-agent in the VM "
    "and enable 'QEMU Guest Agent' in its Proxmox options"
)


def _ct(vmid: int):
    return next(c for c in _CONTAINERS if c[0] == vmid)


class DemoHostClient:
    def __init__(self, catalog: AppCatalog):
        self._catalog = catalog
        self._updated: set[int] = set()
        self._pending = {c[0]: random.sample(_PACKAGES, random.randint(0, 12)) for c in _CONTAINERS}
        self._versions = {c[0]: c[6] for c in _CONTAINERS}
        self._snapshots: dict[int, list[dict]] = {c[0]: [] for c in _CONTAINERS}  # newest first
        # a few vzdump backups made by LUM, newest first
        now = int(time.time())
        self._backups: dict[int, list[dict]] = {c[0]: [] for c in _CONTAINERS}
        for vmid, days, storage, size, protected in [(103, 2, "pbs", 1_843_000_000, 0),
                                                     (103, 9, "pbs", 1_790_000_000, 1),
                                                     (200, 4, "local", 6_200_000_000, 0)]:
            ctime = now - days * 86400
            kind = "qemu" if vmid >= 200 else "lxc"
            self._backups[vmid].append({"id": ctime, "ctime": ctime, "storage": storage, "size": size,
                                        "protected": protected,
                                        "volid": f"{storage}:backup/vzdump-{kind}-{vmid}-{ctime}.tar.zst"})

    async def wrapper_version(self) -> int:
        from . import REQUIRED_WRAPPER_VERSION

        return REQUIRED_WRAPPER_VERSION

    async def list_containers(self) -> list[dict]:
        await asyncio.sleep(0.2)
        return [{"vmid": c[0], "name": c[1], "status": c[2], "tags": c[3], "type": c[7]} for c in _CONTAINERS]

    async def info(self, vmid: int) -> ContainerInfo:
        if vmid in NO_AGENT:
            raise HostCommandError("info", 5, AGENT_ERROR.format(vmid))
        c = _ct(vmid)
        return ContainerInfo(pkg_manager=c[4], community_script=c[5] is not None, script=c[5])

    async def check(self, vmid: int) -> list[str]:
        await asyncio.sleep(random.uniform(0.3, 1.0))
        if _ct(vmid)[4] == "apk":
            return [f"{name}-{old} < {new}" for name, old, new in self._pending[vmid]]
        return [f"{name}/stable-security {new} amd64 [upgradable from: {old}]" for name, old, new in self._pending[vmid]]

    async def app_version(self, vmid: int, app: str) -> str:
        if vmid in self._updated:  # after a demo app update report the real latest version
            src = await self._catalog.source(_ct(vmid)[5])
            return (await self._catalog.latest(src) if src else None) or ""
        return self._versions[vmid]

    async def pkg_version(self, vmid: int, manager: str, package: str) -> str:
        return self._versions[vmid]

    async def _fake_stream(self, lines: list[str], exit_status: int = 0) -> AsyncIterator[str]:
        for line in lines:
            await asyncio.sleep(0.15)
            yield line
        if exit_status:
            raise HostCommandError("demo", exit_status, "simulated")

    def upgrade(self, vmid: int) -> AsyncIterator[str]:
        pkgs, self._pending[vmid] = self._pending[vmid], []
        if _ct(vmid)[4] == "apk":
            lines = ["fetch https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/APKINDEX.tar.gz"]
            lines += [f"({i}/{len(pkgs)}) Upgrading {n} ({o} -> {v})" for i, (n, o, v) in enumerate(pkgs, 1)]
            lines.append(f"OK: 48 MiB in {60 + len(pkgs)} packages")
            return self._fake_stream(lines)
        names = " ".join(n for n, _, _ in pkgs)
        lines = [
            "Hit:1 http://deb.debian.org/debian trixie InRelease",
            "Get:2 http://security.debian.org/debian-security trixie-security InRelease [43.4 kB]",
            "Reading package lists...", "Building dependency tree...", "Reading state information...",
            "Calculating upgrade...",
        ]
        if pkgs:
            lines += ["The following packages will be upgraded:", f"  {names}"]
        lines.append(f"{len(pkgs)} upgraded, 0 newly installed, 0 to remove and 0 not upgraded.")
        for n, o, v in pkgs:
            lines += [f"Unpacking {n} ({v}) over ({o}) ...", f"Setting up {n} ({v}) ..."]
        if pkgs:
            lines.append("Processing triggers for libc-bin (2.41-12+deb13u4) ...")
        if _ct(vmid)[7] == "qemu":
            lines.insert(0, f"running in VM {vmid} through the QEMU guest agent - "
                            "the output appears when the update has finished")
        return self._fake_stream(lines)

    def app_update(self, vmid: int) -> AsyncIterator[str]:
        if vmid == 104:  # show how a deliberately skipped update looks
            return self._fake_stream(["⚠️ Container has 1 CPU / 512 MB, script requires 2 CPU / 1024 MB"], 113)
        return self._app_update_lines(vmid)

    async def _app_update_lines(self, vmid: int) -> AsyncIterator[str]:
        script = _ct(vmid)[5]
        app = script.capitalize()
        src = await self._catalog.source(script)
        latest = (await self._catalog.latest(src) if src else None) or "latest"
        self._updated.add(vmid)
        async for line in self._fake_stream([
            f"✔️ Update available: {app} {self._versions[vmid] or '?'} -> {latest}",
            "⏳ Stopping Service", "✔️ Stopped Service",
            "⏳ Backing up configuration", "✔️ Backed up configuration",
            f"⏳ Updating {app}", f"✔️ Updated {app}",
            "⏳ Starting Service", "✔️ Started Service",
            "✔️ Updated successfully!",
        ]):
            yield line

    def snapshot(self, vmid: int, name: str) -> AsyncIterator[str]:
        if any(x["name"] == name for x in self._snapshots[vmid]):
            return self._fake_stream([f"snapshot name '{name}' already used"], 255)
        self._snapshots[vmid].insert(0, {"name": name, "snaptime": int(time.time())})
        label = "VM" if _ct(vmid)[7] == "qemu" else "CT"
        return self._fake_stream([f"snapshot {name} created for {label} {vmid}"])

    async def snapshots(self, vmid: int) -> list[dict]:
        return list(self._snapshots[vmid])

    def prune_snapshots(self, vmid: int, keep: int) -> AsyncIterator[str]:
        drop = self._snapshots[vmid][keep:]
        del self._snapshots[vmid][keep:]
        return self._fake_stream([f"deleted snapshot {x['name']}" for x in drop])

    async def delete_snapshot(self, vmid: int, name: str) -> None:
        await asyncio.sleep(4)  # like a real one: takes a few seconds
        if not any(x["name"] == name for x in self._snapshots[vmid]):
            raise HostCommandError("delete-snapshot", 2, f"snapshot {name} does not exist (anymore)")
        self._snapshots[vmid] = [x for x in self._snapshots[vmid] if x["name"] != name]

    async def backups(self, vmid: int) -> list[dict]:
        return list(self._backups[vmid])

    async def delete_backup(self, vmid: int, backup_id: int) -> None:
        await asyncio.sleep(7)  # pvesm free on a PBS takes a while
        match = [b for b in self._backups[vmid] if b["id"] == backup_id]
        if len(match) != 1:
            raise HostCommandError("delete-backup", 2, f"no single LUM backup with id {backup_id}")
        if match[0]["protected"]:
            raise HostCommandError("delete-backup", 2, f"backup {match[0]['volid']} is protected")
        self._backups[vmid].remove(match[0])

    def restore_backup(self, vmid: int, backup_id: int) -> AsyncIterator[str]:
        match = [b for b in self._backups[vmid] if b["id"] == backup_id]
        if len(match) != 1:
            return self._fake_stream([f"error: no single LUM backup with id {backup_id}"], 2)
        self._snapshots[vmid] = []  # Proxmox drops the snapshots on a restore
        label = "VM" if _ct(vmid)[7] == "qemu" else "container"
        return self._fake_stream([
            f"shutting down {label} {vmid}", f"restoring {label} {vmid} from {match[0]['volid']}",
            "extracting archive …", "Total bytes read: 1843000000 (1.8GiB)",
            f"starting {label} {vmid}", "restore done",
        ])

    def rollback(self, vmid: int, name: str) -> AsyncIterator[str]:
        if not any(x["name"] == name for x in self._snapshots[vmid]):
            return self._fake_stream([f"error: snapshot {name} does not exist (anymore)"], 2)
        self._updated.discard(vmid)
        self._versions[vmid] = _ct(vmid)[6]
        return self._fake_stream([
            f"shutting down container {vmid}", f"rolling back to {name}",
            f"starting container {vmid}", "rollback done",
        ])

    def backup(self, vmid: int, storage: str, mode: str) -> AsyncIterator[str]:
        ctime = int(time.time())
        self._backups[vmid].insert(0, {"id": ctime, "ctime": ctime, "storage": storage, "size": 1_500_000_000,
                                       "protected": 0, "volid": f"{storage}:backup/vzdump-{vmid}-{ctime}.tar.zst"})
        return self._fake_stream([
            f"INFO: starting new backup job: vzdump {vmid} --storage {storage} --mode {mode}",
            f"INFO: creating vzdump archive 'vzdump-lxc-{vmid}-{time.strftime('%Y_%m_%d-%H_%M_%S')}.tar.zst'",
            "INFO: Finished Backup of VM",
        ])

    def prune_backups(self, vmid: int, storage: str, keep: int) -> AsyncIterator[str]:
        return self._fake_stream([])
