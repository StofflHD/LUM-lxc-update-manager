"""Client for the lxc-update-wrapper on the Proxmox host.

Every call sends a single verb line; the forced command on the host decides
what actually runs. Arguments are validated here as well so nothing with
whitespace or shell metacharacters ever leaves this process.
"""

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncssh

from .config import Settings

_SAFE_ARG = re.compile(r"^[A-Za-z0-9._-]{1,60}$")


OUTDATED_HINT = (
    "the host script on the Proxmox host is too old for this LUM version - run on the host: "
    "bash <(curl -fsSL https://raw.githubusercontent.com/StofflHD/LUM-lxc-update-manager/main/install.sh) --update"
)


class HostCommandError(Exception):
    def __init__(self, verb: str, exit_status: int | None, stderr: str):
        # an old wrapper rejects verbs it doesn't know - say what to do instead
        if "verb not allowed" in stderr:
            stderr = OUTDATED_HINT
        super().__init__(f"{verb} failed ({exit_status}): {stderr.strip()}")
        self.exit_status = exit_status


@dataclass
class ContainerInfo:
    pkg_manager: str
    community_script: bool
    script: str | None  # community-scripts ct script name, e.g. "tandoor"


def _command(verb: str, *args: str | int) -> str:
    parts = [verb, *(str(a) for a in args)]
    for p in parts:
        if not _SAFE_ARG.match(p):
            raise ValueError(f"unsafe argument: {p!r}")
    return " ".join(parts)


class HostClient:
    def __init__(self, settings: Settings):
        self._s = settings

    def _connect(self):
        return asyncssh.connect(
            self._s.pve_host,
            port=self._s.pve_port,
            username=self._s.pve_user,
            client_keys=[str(self._s.ssh_key_path)],
            known_hosts=str(self._s.known_hosts_path),
        )

    async def _run(self, verb: str, *args: str | int, timeout: float = 300) -> str:
        async with self._connect() as conn:
            result = await conn.run(_command(verb, *args), check=False, timeout=timeout)
        if result.exit_status != 0:
            raise HostCommandError(verb, result.exit_status, str(result.stderr or ""))
        return str(result.stdout or "")

    async def _stream(self, verb: str, *args: str | int) -> AsyncIterator[str]:
        async with self._connect() as conn:
            proc = await conn.create_process(_command(verb, *args), stderr=asyncssh.STDOUT)
            last = ""
            async for line in proc.stdout:
                # progress output (dpkg "Reading database ... 5%") rewrites one
                # line with \r; keep only its final state
                last = line.rstrip("\r\n").split("\r")[-1]
                yield last
            done = await proc.wait()
        if done.exit_status != 0:
            raise HostCommandError(verb, done.exit_status, last if "verb not allowed" in last else "see log")

    # --- verbs -----------------------------------------------------------

    async def wrapper_version(self) -> int:
        """0 for host scripts from before the version verb existed."""
        try:
            return int((await self._run("version", timeout=30)).strip() or 0)
        except HostCommandError:
            return 0

    async def list_containers(self) -> list[dict]:
        return json.loads(await self._run("list"))

    async def info(self, vmid: int) -> ContainerInfo:
        values = dict(
            line.split("=", 1) for line in (await self._run("info", vmid)).splitlines() if "=" in line
        )
        return ContainerInfo(
            pkg_manager=values.get("pkg", "unknown"),
            community_script=values.get("community") == "1",
            script=values.get("script") or None,
        )

    async def check(self, vmid: int) -> list[str]:
        out = await self._run("check", vmid, timeout=600)
        return [line.strip() for line in out.splitlines() if line.strip()]

    async def app_version(self, vmid: int, app: str) -> str:
        return (await self._run("app-version", vmid, app)).strip()

    async def pkg_version(self, vmid: int, manager: str, package: str) -> str:
        """Installed version of a pip / npm package ('' if not installed)."""
        return (await self._run("pkg-version", vmid, manager, package)).strip()

    async def restart_needed(self, vmid: int) -> tuple[bool, list[str]]:
        """(reboot flag / newer kernel, services still using replaced libraries)"""
        out = await self._run("restart-needed", vmid, timeout=180)
        values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        return values.get("reboot") == "1", values.get("services", "").split()

    def restart(self, vmid: int) -> AsyncIterator[str]:
        return self._stream("restart", vmid)

    def upgrade(self, vmid: int) -> AsyncIterator[str]:
        return self._stream("upgrade", vmid)

    def app_update(self, vmid: int) -> AsyncIterator[str]:
        return self._stream("app-update", vmid)

    def snapshot(self, vmid: int, name: str) -> AsyncIterator[str]:
        return self._stream("snapshot", vmid, name)

    async def snapshots(self, vmid: int) -> list[dict]:
        """lum_* snapshots, newest first: [{"name", "snaptime"}]"""
        return json.loads(await self._run("snapshots", vmid))

    def prune_snapshots(self, vmid: int, keep: int) -> AsyncIterator[str]:
        return self._stream("prune-snapshots", vmid, keep)

    async def delete_snapshot(self, vmid: int, name: str) -> None:
        await self._run("delete-snapshot", vmid, name)

    async def backups(self, vmid: int) -> list[dict]:
        """vzdump backups made by LUM, newest first: [{"id", "volid", "storage", "ctime", "size", "protected"}]"""
        return json.loads(await self._run("backups", vmid, timeout=120))

    async def delete_backup(self, vmid: int, backup_id: int) -> None:
        await self._run("delete-backup", vmid, backup_id, timeout=300)

    def restore_backup(self, vmid: int, backup_id: int) -> AsyncIterator[str]:
        return self._stream("restore-backup", vmid, backup_id)

    def rollback(self, vmid: int, name: str) -> AsyncIterator[str]:
        return self._stream("rollback", vmid, name)

    def backup(self, vmid: int, storage: str, mode: str) -> AsyncIterator[str]:
        return self._stream("backup", vmid, storage, mode)

    def prune_backups(self, vmid: int, storage: str, keep: int) -> AsyncIterator[str]:
        return self._stream("prune-backups", vmid, storage, keep)
