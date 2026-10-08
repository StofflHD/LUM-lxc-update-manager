"""Fake host for UI development without a Proxmox server (LUM_DEMO=true).

Containers are simulated; the app version lookup (ct script + GitHub release)
still goes to the real community-scripts repo and GitHub.
"""

import asyncio
import random
import time
from collections.abc import AsyncIterator

from .apps import AppCatalog
from .host import ContainerInfo, HostCommandError

# vmid, name, status, tags, pkg manager, ct script, installed app version
_CONTAINERS = [
    (101, "homeassistant", "running", "community-script;smarthome", "apt", "homeassistant", ""),
    (102, "adguard", "running", "community-script;network", "apt", "adguard", "0.107.40"),
    (103, "tandoor", "running", "community-script;recipes", "apt", "tandoor", "2.0.0"),
    (104, "vaultwarden", "running", "community-script", "apk", "vaultwarden", "1.30.0"),
    (105, "test-debian", "stopped", "", "apt", None, ""),
]


def _ct(vmid: int):
    return next(c for c in _CONTAINERS if c[0] == vmid)


class DemoHostClient:
    def __init__(self, catalog: AppCatalog):
        self._catalog = catalog
        self._updated: set[int] = set()
        self._pending = {c[0]: random.randint(0, 25) for c in _CONTAINERS}
        self._versions = {c[0]: c[6] for c in _CONTAINERS}
        self._snapshots: dict[int, list[dict]] = {c[0]: [] for c in _CONTAINERS}  # newest first

    async def list_containers(self) -> list[dict]:
        await asyncio.sleep(0.2)
        return [{"vmid": c[0], "name": c[1], "status": c[2], "tags": c[3]} for c in _CONTAINERS]

    async def info(self, vmid: int) -> ContainerInfo:
        c = _ct(vmid)
        return ContainerInfo(pkg_manager=c[4], community_script=c[5] is not None, script=c[5])

    async def check(self, vmid: int) -> list[str]:
        await asyncio.sleep(random.uniform(0.3, 1.0))
        return [f"pkg{i}/stable 1.{i}.1 amd64 [upgradable from: 1.{i}.0]" for i in range(self._pending[vmid])]

    async def app_version(self, vmid: int, app: str) -> str:
        if vmid in self._updated:  # after a demo app update report the real latest version
            src = await self._catalog.source(_ct(vmid)[5])
            return (await self._catalog.latest(src.repo) if src else None) or ""
        return self._versions[vmid]

    async def _fake_stream(self, lines: list[str], exit_status: int = 0) -> AsyncIterator[str]:
        for line in lines:
            await asyncio.sleep(0.15)
            yield line
        if exit_status:
            raise HostCommandError("demo", exit_status, "simulated")

    def upgrade(self, vmid: int) -> AsyncIterator[str]:
        n = self._pending[vmid]
        self._pending[vmid] = 0
        lines = ["Reading package lists...", f"{n} upgraded, 0 newly installed, 0 to remove."]
        lines[1:1] = [f"Setting up pkg{i} (1.{i}.1) ..." for i in range(n)]
        return self._fake_stream(lines)

    def app_update(self, vmid: int) -> AsyncIterator[str]:
        if vmid == 104:  # show how a deliberately skipped update looks
            return self._fake_stream(["⚠️ Container has 1 CPU / 512 MB, script requires 2 CPU / 1024 MB"], 113)
        self._updated.add(vmid)
        return self._fake_stream([
            "✔️ Update available: app -> latest",
            "⏳ Stopping Service", "✔️ Stopped Service",
            "⏳ Updating", "✔️ Updated",
            "⏳ Starting Service", "✔️ Started Service",
            "✔️ Updated successfully!",
        ])

    def snapshot(self, vmid: int, name: str) -> AsyncIterator[str]:
        if any(x["name"] == name for x in self._snapshots[vmid]):
            return self._fake_stream([f"snapshot name '{name}' already used"], 255)
        self._snapshots[vmid].insert(0, {"name": name, "snaptime": int(time.time())})
        return self._fake_stream([f"snapshot {name} created for CT {vmid}"])

    async def snapshots(self, vmid: int) -> list[dict]:
        return list(self._snapshots[vmid])

    def prune_snapshots(self, vmid: int, keep: int) -> AsyncIterator[str]:
        drop = self._snapshots[vmid][keep:]
        del self._snapshots[vmid][keep:]
        return self._fake_stream([f"deleted snapshot {x['name']}" for x in drop])

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
        return self._fake_stream([
            f"INFO: starting new backup job: vzdump {vmid} --storage {storage} --mode {mode}",
            f"INFO: creating vzdump archive 'vzdump-lxc-{vmid}-{time.strftime('%Y_%m_%d-%H_%M_%S')}.tar.zst'",
            "INFO: Finished Backup of VM",
        ])

    def prune_backups(self, vmid: int, storage: str, keep: int) -> AsyncIterator[str]:
        return self._fake_stream([])
