"""Update checks and update jobs."""

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field

from .apps import AppCatalog, strip_v
from .config import Settings
from .db import Database
from .host import HostCommandError

log = logging.getLogger(__name__)

# exit codes of the community-scripts update with PHS_SILENT=1 that mean
# "skipped on purpose", see tools/pve/update-apps.sh
APP_UPDATE_SKIPPED = {
    75: "skipped: the update needs interactive mode (run 'update' manually inside the container)",
    113: "skipped: the container has less CPU/RAM than the script requires (increase resources)",
    114: "skipped: /boot storage almost full (>80 %)",
}


@dataclass
class Job:
    id: int
    vmid: int
    kind: str  # "os" | "app" | "rollback"
    target: str | None = None  # rollback: snapshot name
    backup: bool = True  # update: make a snapshot/vzdump first (LUM_BACKUP_MODE)
    lines: list[str] = field(default_factory=list)
    done: bool = False
    success: bool | None = None
    _listeners: set[asyncio.Queue] = field(default_factory=set)

    def emit(self, line: str) -> None:
        self.lines.append(line)
        for q in self._listeners:
            q.put_nowait(line)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        for line in self.lines:
            q.put_nowait(line)
        if self.done:
            q.put_nowait(None)
        else:
            self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    def finish(self, success: bool) -> None:
        self.done, self.success = True, success
        for q in self._listeners:
            q.put_nowait(None)
        self._listeners.clear()

    def as_dict(self) -> dict:
        return {
            "id": self.id, "vmid": self.vmid, "kind": self.kind, "target": self.target, "backup": self.backup,
            "done": self.done, "success": self.success,
        }


class UpdateService:
    def __init__(self, settings: Settings, db: Database, host, catalog: AppCatalog):
        self._s = settings
        self.db = db
        self.host = host
        self.catalog = catalog
        self.jobs: dict[int, Job] = {}
        self._job_ids = itertools.count(1)
        self._busy: set[int] = set()  # vmids with a running job
        self._check_sem = asyncio.Semaphore(settings.max_parallel_checks)
        self.last_refresh: float | None = None
        self.wrapper_version: int | None = None  # host script version, None = unknown
        self.refreshing = False

    # --- checks ------------------------------------------------------------

    async def refresh_all(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        try:
            try:
                self.wrapper_version = await self.host.wrapper_version()
            except (OSError, ValueError) as err:
                log.warning("cannot read host script version: %s", err)
            containers = await self.host.list_containers()
            self.db.sync_containers(containers)
            running = [int(c["vmid"]) for c in containers if c.get("status") == "running"]
            await asyncio.gather(*(self.check(vmid) for vmid in running if vmid not in self._busy))
            self.last_refresh = time.time()
        finally:
            self.refreshing = False

    async def check(self, vmid: int) -> None:
        async with self._check_sem:
            try:
                info = await self.host.info(vmid)
                packages = await self.host.check(vmid)
                self.db.set_check_result(vmid, info.pkg_manager, info.community_script, packages, None)
            except (HostCommandError, OSError, ValueError) as err:
                log.warning("check %s failed: %s", vmid, err)
                c = self.db.container(vmid) or {}
                self.db.set_check_result(
                    vmid, None, c.get("community_script", False), c.get("upgradable", []), str(err)
                )
                return
            if info.script:
                await self._check_app(vmid, info.script)

    async def _check_app(self, vmid: int, script: str) -> None:
        """Installed vs. latest upstream version; failures only leave the fields empty."""
        repo = installed = latest = None
        try:
            src = await self.catalog.source(script)
            if src:
                repo = src.repo
                installed = strip_v(await self.host.app_version(vmid, src.app)) or None
                latest = await self.catalog.latest(src.repo)
        except (HostCommandError, OSError, ValueError) as err:
            log.warning("app check %s (%s) failed: %s", vmid, script, err)
        self.db.set_app_result(vmid, script, repo, installed, latest)

    # --- jobs --------------------------------------------------------------

    def start_job(self, vmid: int, kind: str, target: str | None = None, backup: bool = True) -> Job:
        """kind: "os" | "app" update, or "rollback" to the lum_ snapshot <target>.
        backup=False skips the safety copy for this one update."""
        if vmid in self._busy:
            raise RuntimeError(f"container {vmid} already has a running job")
        job = Job(id=next(self._job_ids), vmid=vmid, kind=kind, target=target, backup=backup)
        self.jobs[job.id] = job
        self._busy.add(vmid)
        run = self._run_rollback if kind == "rollback" else self._run_update
        asyncio.create_task(self._guarded(job, run))
        return job

    async def _guarded(self, job: Job, run) -> None:
        history_id = self.db.start_history(job.vmid, job.kind, job.target)
        success = False
        try:
            success = await run(job, history_id)
        except HostCommandError as err:
            if job.kind == "app" and err.exit_status in APP_UPDATE_SKIPPED:
                job.emit(f"### {APP_UPDATE_SKIPPED[err.exit_status]}")
                # nothing was changed, so the copy we just made is safe to rotate
                if job.backup:
                    await self._prune(job)
            else:
                job.emit(f"### Error: {err}")
        except (OSError, ValueError) as err:
            job.emit(f"### Error: {err}")
        except Exception as err:  # keep the job state consistent no matter what
            log.exception("job %s crashed", job.id)
            job.emit(f"### Internal error: {err}")
        finally:
            self.db.finish_history(history_id, success, "\n".join(job.lines))
            self._busy.discard(job.vmid)
            job.finish(success)
        await self.check(job.vmid)

    async def _run_update(self, job: Job, history_id: int) -> bool:
        if job.backup:
            await self._make_backup(job, history_id)
        else:
            job.emit("### No backup (turned off for this update)")

        job.emit("### OS update" if job.kind == "os" else "### App update (PHS_SILENT=1)")
        stream = self.host.upgrade(job.vmid) if job.kind == "os" else self.host.app_update(job.vmid)
        async for line in stream:
            job.emit(line)

        # only after success: after a failure every copy may still be needed
        if job.backup:
            await self._prune(job)
        job.emit("### Done")
        return True

    async def _make_backup(self, job: Job, history_id: int) -> None:
        """Raises on failure, so the update never runs without its safety copy."""
        mode = self._s.backup_mode
        if mode == "snapshot":
            name = time.strftime("lum_%Y%m%d_%H%M%S")
            job.emit(f"### Snapshot {name}")
            async for line in self.host.snapshot(job.vmid, name):
                job.emit(line)
            self.db.set_history_backup(history_id, "snapshot", name)
        elif mode == "vzdump":
            storage = self._s.backup_storage
            job.emit(f"### Backup (vzdump, {self._s.backup_vzdump_mode}) to {storage}")
            async for line in self.host.backup(job.vmid, storage, self._s.backup_vzdump_mode):
                job.emit(line)
            self.db.set_history_backup(history_id, "vzdump", storage)
        else:
            job.emit("### No backup (LUM_BACKUP_MODE=none)")

    async def _prune(self, job: Job) -> None:
        """Rotate the manager's own snapshots/backups; failures here never fail the job."""
        mode = self._s.backup_mode
        try:
            if mode == "snapshot":
                job.emit(f"### Cleanup: keeping the newest {self._s.snapshot_keep} snapshots")
                removed = []
                async for line in self.host.prune_snapshots(job.vmid, self._s.snapshot_keep):
                    job.emit(line)
                    if line.startswith("deleted snapshot "):
                        removed.append(line.removeprefix("deleted snapshot ").strip())
                self.db.mark_snapshots_removed(job.vmid, removed)
            elif mode == "vzdump":
                storage, keep = self._s.backup_storage, self._s.backup_keep
                job.emit(f"### Cleanup: keeping the newest {keep} backups")
                async for line in self.host.prune_backups(job.vmid, storage, keep):
                    job.emit(line)
                self.db.mark_vzdump_pruned(job.vmid, storage, keep)
        except (HostCommandError, OSError, ValueError) as err:
            job.emit(f"### Warning: cleanup failed: {err}")

    async def delete_snapshot(self, vmid: int, name: str) -> None:
        """Delete one lum_ snapshot; blocks the container like a job meanwhile."""
        if vmid in self._busy:
            raise RuntimeError(f"container {vmid} has a running job")
        self._busy.add(vmid)
        try:
            await self.host.delete_snapshot(vmid, name)
            self.db.mark_snapshots_removed(vmid, [name])
        finally:
            self._busy.discard(vmid)

    async def _run_rollback(self, job: Job, history_id: int) -> bool:
        job.emit(f"### Rollback to snapshot {job.target}")
        async for line in self.host.rollback(job.vmid, job.target):
            job.emit(line)
        job.emit("### Done")
        return True

    def busy(self, vmid: int) -> bool:
        return vmid in self._busy


async def scheduler(service: UpdateService, interval_minutes: int) -> None:
    while True:
        try:
            await service.refresh_all()
        except Exception:
            log.exception("scheduled refresh failed")
        await asyncio.sleep(interval_minutes * 60)
