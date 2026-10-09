"""Update checks and update jobs."""

import asyncio
import itertools
import logging
import re
import socket
import time
from dataclasses import dataclass, field

from .apps import AppCatalog, normalize
from .config import Settings
from .db import Database
from .host import HostCommandError

log = logging.getLogger(__name__)

EXCLUDE_TAG = "no-lum"  # Proxmox tag: LUM leaves this container / VM alone

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
    kind: str  # "os" | "app" | "rollback" | "restore" | "restart"
    target: str | None = None  # rollback: snapshot name, restore: backup id (ctime)
    backup: bool = True  # update: make a snapshot/vzdump first (LUM_BACKUP_MODE)
    cleanup: bool = True  # OS update: apt autoremove + apt clean afterwards
    lines: list[str] = field(default_factory=list)
    done: bool = False
    success: bool | None = None
    _listeners: set[asyncio.Queue] = field(default_factory=set)
    _finished: asyncio.Event = field(default_factory=asyncio.Event)

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
        self._finished.set()

    async def wait(self) -> None:
        await self._finished.wait()

    def as_dict(self) -> dict:
        return {
            "id": self.id, "vmid": self.vmid, "kind": self.kind, "target": self.target, "backup": self.backup,
            "cleanup": self.cleanup,
            "done": self.done, "success": self.success,
        }


@dataclass
class QueueItem:
    """One guest in the update queue (several guests updated one after the other)."""
    vmid: int
    kind: str  # "os" | "app"
    backup: bool
    cleanup: bool = True
    state: str = "waiting"  # waiting | running | ok | failed | skipped | cancelled
    job_id: int | None = None
    note: str = ""

    def as_dict(self) -> dict:
        return {"vmid": self.vmid, "kind": self.kind, "backup": self.backup, "cleanup": self.cleanup, "state": self.state,
                "job_id": self.job_id, "note": self.note}


@dataclass
class Cleanup:
    """Deleting all of LUM's snapshots and/or vzdump backups (from "Clear history")."""
    snapshots: bool
    backups: bool
    state: str = "collecting"  # collecting | deleting | done
    total: int = 0
    done: int = 0
    deleted_snapshots: int = 0
    deleted_backups: int = 0
    skipped: list[str] = field(default_factory=list)  # protected, guest busy
    failed: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def as_dict(self) -> dict:
        return {
            "snapshots": self.snapshots, "backups": self.backups, "state": self.state,
            "total": self.total, "done": self.done, "deleted_snapshots": self.deleted_snapshots,
            "deleted_backups": self.deleted_backups, "skipped": self.skipped, "failed": self.failed,
            "elapsed": round((self.finished or time.time()) - self.started),
            "since_finished": round(time.time() - self.finished) if self.finished else None,
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
        self.hidden: list[int] = []  # guests tagged EXCLUDE_TAG
        self.refreshing = False
        self.queue: list[QueueItem] = []
        self._queue_task: asyncio.Task | None = None
        self.cleanup: Cleanup | None = None
        self.self_vmid: int | None = None  # the container LUM runs in, if it is on this host
        self._find_self([{"vmid": c["vmid"], "name": c["name"]} for c in db.containers()])
        self._close_interrupted()

    def _find_self(self, guests: list[dict]) -> None:
        """LUM's own container: the one named like this host (only if exactly one is)."""
        me = socket.gethostname().split(".")[0].lower()
        found = [int(g["vmid"]) for g in guests if str(g.get("name", "")).lower() == me]
        self.self_vmid = found[0] if len(found) == 1 else None

    def _close_interrupted(self) -> None:
        """Jobs still "running" in the history were cut off when LUM stopped - e.g. when
        it restarted its own container. Close them so they don't stay "running"."""
        for h in self.db.unfinished_history():
            own_restart = h["kind"] == "restart" and h["vmid"] == self.self_vmid
            note = ("### LUM's own container was restarted - LUM is running again, so the restart worked"
                    if own_restart else
                    "### Interrupted: LUM was stopped or restarted while this ran - check the guest")
            self.db.finish_history(h["id"], own_restart, note)
            if own_restart:
                self.db.set_restart(h["vmid"], False, [])
            log.warning("history entry %s (%s of %s) was interrupted by a LUM restart", h["id"], h["kind"], h["vmid"])

    # --- checks ------------------------------------------------------------

    def _managed(self, guests: list[dict]) -> list[dict]:
        """Drop guests tagged "no-lum" (the host script refuses them as well)."""
        keep, hidden = [], []
        for g in guests:
            tags = {t.lower() for t in re.split(r"[;, ]+", g.get("tags") or "") if t}
            (hidden if EXCLUDE_TAG in tags else keep).append(g)
        self.hidden = sorted(int(g["vmid"]) for g in hidden)
        return keep

    def _apply_list(self, guests: list[dict]) -> tuple[list[int], list[int], list[int]]:
        """Store the guest list from the host. Returns (added, removed, restarted):
        restarted = running again with a lower uptime, or started - whoever did it
        (LUM, the Proxmox UI, a host reboot). A restart resolves "restart required"."""
        old = {c["vmid"]: c for c in self.db.containers()}
        restarted = []
        for g in guests:
            vmid, before = int(g["vmid"]), old.get(int(g["vmid"]))
            if not before or g.get("status") != "running":
                continue
            up, up_before = g.get("uptime"), before.get("uptime")
            if before["status"] != "running" or (up is not None and up_before is not None and up < up_before):
                restarted.append(vmid)
        self.db.sync_containers(guests)
        self._find_self(guests)
        for vmid in restarted:
            self.db.set_restart(vmid, False, [])
        now = {int(g["vmid"]) for g in guests}
        return sorted(now - set(old)), sorted(set(old) - now), restarted

    async def poll_status(self) -> None:
        """Cheap and frequent (one "list"): status changes, restarts done outside LUM,
        new and removed guests. Restarted and new running guests are checked."""
        if self.refreshing:
            return
        guests = self._managed(await self.host.list_containers())
        added, _, restarted = self._apply_list(guests)
        running = {int(g["vmid"]) for g in guests if g.get("status") == "running"}
        for vmid in sorted(set(added + restarted) & running):
            if vmid not in self._busy:
                asyncio.create_task(self.check(vmid))

    async def refresh_all(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        try:
            try:
                self.wrapper_version = await self.host.wrapper_version()
            except (OSError, ValueError) as err:
                log.warning("cannot read host script version: %s", err)
            containers = self._managed(await self.host.list_containers())
            self._apply_list(containers)
            running = [int(c["vmid"]) for c in containers if c.get("status") == "running"]
            await asyncio.gather(*(self.check(vmid) for vmid in running if vmid not in self._busy))
            self.last_refresh = time.time()
        finally:
            self.refreshing = False

    async def sync_guests(self) -> dict:
        """Re-read only the list of containers/VMs from the host - no package checks.
        New running guests get checked in the background so they show their state."""
        guests = self._managed(await self.host.list_containers())
        added, removed, restarted = self._apply_list(guests)
        for g in guests:
            vmid = int(g["vmid"])
            if vmid in added + restarted and g.get("status") == "running" and vmid not in self._busy:
                asyncio.create_task(self.check(vmid))
        return {"added": added, "removed": removed, "hidden": self.hidden}

    async def check(self, vmid: int) -> None:
        async with self._check_sem:
            try:
                info = await self.host.info(vmid)
                await self._check_restart(vmid)
                packages = await self.host.check(vmid)
                self.db.set_check_result(vmid, info.pkg_manager, info.community_script, packages, None)
            except (HostCommandError, OSError, ValueError) as err:
                log.warning("check %s failed: %s", vmid, err)
                c = self.db.container(vmid) or {}
                self.db.set_check_result(
                    vmid, None, c.get("community_script", False), c.get("upgradable", []), str(err)
                )
                return
            await self._check_disk(vmid)
            if info.script and not (self.db.container(vmid) or {}).get("self_created"):
                await self._check_app(vmid, info.script)

    async def _check_disk(self, vmid: int) -> None:
        """Free space in the guest for the "low disk" hint; unknown with host scripts < 8."""
        try:
            sp = await self.host.space(vmid)
            self.db.set_disk(vmid, sp.get("guest_avail_kb"), sp.get("guest_size_kb"))
        except (HostCommandError, OSError, ValueError) as err:
            log.debug("disk check %s: %s", vmid, err)
            self.db.set_disk(vmid, None, None)

    def low_disk(self, c: dict) -> bool:
        free = c.get("disk_free_kb")
        return bool(self._s.min_free_mb) and free is not None and free < self._s.min_free_mb * 1024

    async def _check_space(self, job: Job) -> None:
        """Before an update: enough room in the guest, and on the vzdump storage for the
        backup? Raises ValueError (the update doesn't start) if not. Can't be checked
        (e.g. host script < 8): a warning, the update runs anyway."""
        storage = self._s.backup_storage if job.backup and self._s.backup_mode == "vzdump" else ""
        try:
            sp = await self.host.space(job.vmid, storage)
        except (HostCommandError, OSError, ValueError) as err:
            job.emit(f"### Warning: free space not checked: {err}")
            return
        free_mb = sp.get("guest_avail_kb", 0) // 1024
        self.db.set_disk(job.vmid, sp.get("guest_avail_kb"), sp.get("guest_size_kb"))
        need_mb = self._s.min_free_mb
        if need_mb:
            job.emit(f"### Free space in /: {free_mb} MB (needs {need_mb} MB, LUM_MIN_FREE_MB)")
            if free_mb < need_mb:
                raise ValueError(
                    f"only {free_mb} MB free in / of the guest, the update needs at least {need_mb} MB "
                    "(LUM_MIN_FREE_MB) - free up space (e.g. apt clean, old logs) or enlarge the disk"
                )
        if storage and sp.get("storage_type") != "pbs":  # PBS deduplicates: a backup needs little
            avail = sp.get("storage_avail_kb", 0) * 1024
            # the next backup will be about as big as the last one; without one estimate
            # from the guest's data (zstd compresses a system to roughly half)
            need = sp.get("last_backup_bytes") or int(sp.get("guest_used_bytes", 0) * 0.6)
            job.emit(f"### Free space on {storage}: {avail / 1e9:.1f} GB"
                     + (f" (backup needs about {need / 1e9:.1f} GB)" if need else ""))
            if need and avail < need * 1.1:
                raise ValueError(
                    f"only {avail / 1e9:.1f} GB free on the vzdump storage {storage}, the backup needs about "
                    f"{need / 1e9:.1f} GB - free up space there, or untick the backup for this update"
                )

    async def _check_restart(self, vmid: int) -> None:
        """Does the guest need a restart after updates? Unknown with host scripts < 7."""
        try:
            reboot, services = await self.host.restart_needed(vmid)
        except (HostCommandError, OSError, ValueError) as err:
            log.debug("restart check %s: %s", vmid, err)
            self.db.set_restart(vmid, None, None)
            return
        self.db.set_restart(vmid, reboot, services)

    async def _check_app(self, vmid: int, script: str) -> None:
        """Installed vs. latest app version; failures only leave the fields empty."""
        src = await self.catalog.source(script)
        if not src:
            self.db.set_app_result(vmid, script, None, None, None)
            return
        installed = None
        try:
            if src.kind in ("github", "codeberg", "gitlab", "gh_tag"):
                installed = await self.host.app_version(vmid, src.app)
            elif src.kind in ("pypi", "npm"):
                installed = await self.host.pkg_version(vmid, "pip" if src.kind == "pypi" else "npm", src.repo)
        except (HostCommandError, OSError, ValueError) as err:  # e.g. host script < 4: no pkg-version
            log.warning("installed version of %s in %s: %s", script, vmid, err)
        latest = await self.catalog.latest(src)
        note = None
        if src.pinned:
            note = "held back by the community script" + (f": {src.pin_reason}" if src.pin_reason else "")
        self.db.set_app_result(vmid, script, src.label or None, normalize(installed or "") or None, latest,
                               src.kind, src.url or None, note)

    # --- jobs --------------------------------------------------------------

    def start_job(self, vmid: int, kind: str, target: str | None = None, backup: bool = True,
                  cleanup: bool = True) -> Job:
        """kind: "os" | "app" update, or "rollback" to the lum_ snapshot <target>.
        backup=False skips the safety copy for this one update, cleanup=False the
        apt autoremove / clean after an OS update."""
        if vmid in self._busy:
            raise RuntimeError(f"container {vmid} already has a running job")
        job = Job(id=next(self._job_ids), vmid=vmid, kind=kind, target=target, backup=backup, cleanup=cleanup)
        self.jobs[job.id] = job
        self._busy.add(vmid)
        run = {"rollback": self._run_rollback, "restore": self._run_restore,
               "restart": self._run_restart}.get(kind, self._run_update)
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
        if job.kind == "restart":  # the guest may still be booting: try again a few times
            for _ in range(3):
                if not (self.db.container(job.vmid) or {}).get("last_error"):
                    break
                await asyncio.sleep(15)
                await self.check(job.vmid)

    async def _run_update(self, job: Job, history_id: int) -> bool:
        await self._check_space(job)
        if job.backup:
            await self._make_backup(job, history_id)
        else:
            job.emit("### No backup (turned off for this update)")

        job.emit("### OS update" if job.kind == "os" else "### App update (PHS_SILENT=1)")
        stream = self.host.upgrade(job.vmid, job.cleanup) if job.kind == "os" else self.host.app_update(job.vmid)
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

    async def delete_backup(self, vmid: int, backup_id: int) -> None:
        """Delete one vzdump backup made by LUM; blocks the guest like a job meanwhile."""
        if vmid in self._busy:
            raise RuntimeError(f"container {vmid} has a running job")
        self._busy.add(vmid)
        try:
            await self.host.delete_backup(vmid, backup_id)
            self.db.mark_backup_removed(vmid, backup_id)
        finally:
            self._busy.discard(vmid)

    async def delete_history_backup(self, entry: dict) -> int | None:
        """Delete the vzdump backup an update in the history made: the LUM backup on
        its storage whose ctime falls into the job's runtime. Returns its id, or None
        if it no longer exists (then the entry is just marked as removed)."""
        vmid, started = entry["vmid"], entry["started"]
        finished = entry["finished"] or started
        found = [
            b for b in await self.host.backups(vmid)
            if b["storage"] == entry["backup_ref"] and started - 60 <= b["ctime"] <= finished + 60
        ]
        if not found:
            self.db.mark_history_backup_removed(entry["id"])
            return None
        if len(found) > 1:
            raise ValueError("several backups match this entry – delete it under the Backups button")
        if found[0]["protected"]:
            raise ValueError("the backup is protected in Proxmox – remove the protection there first")
        await self.delete_backup(vmid, found[0]["id"])
        return found[0]["id"]

    async def _run_restore(self, job: Job, history_id: int) -> bool:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(job.target)))
        job.emit(f"### Restore from the vzdump backup of {when}")
        async for line in self.host.restore_backup(job.vmid, int(job.target)):
            job.emit(line)
        self.db.mark_all_snapshots_removed(job.vmid)  # a restore drops all snapshots
        job.emit("### Done")
        return True

    async def _run_restart(self, job: Job, history_id: int) -> bool:
        job.emit("### Restart")
        if job.vmid == self.self_vmid:
            # our own container: the host reboots it a few seconds after this job is stored
            job.emit("This is the container LUM runs in - LUM restarts with it and is back in about half a minute.")
            async for line in self.host.restart(job.vmid, background=True):
                job.emit(line)
            self.db.set_restart(job.vmid, False, [])
            job.emit("### Done")
            return True
        async for line in self.host.restart(job.vmid):
            job.emit(line)
        self.db.set_restart(job.vmid, False, [])  # done - the next check confirms it
        job.emit("### Done")
        return True

    async def _run_rollback(self, job: Job, history_id: int) -> bool:
        job.emit(f"### Rollback to snapshot {job.target}")
        async for line in self.host.rollback(job.vmid, job.target):
            job.emit(line)
        job.emit("### Done")
        return True

    def busy(self, vmid: int) -> bool:
        return vmid in self._busy

    # --- delete all of LUM's snapshots / backups ------------------------------------

    @property
    def cleanup_active(self) -> bool:
        return self.cleanup is not None and self.cleanup.state != "done"

    def start_cleanup(self, snapshots: bool, backups: bool) -> Cleanup:
        if self.cleanup_active:
            raise RuntimeError("snapshots and backups are already being deleted")
        self.cleanup = Cleanup(snapshots, backups)
        asyncio.create_task(self._run_cleanup(self.cleanup))
        return self.cleanup

    async def _run_cleanup(self, cl: Cleanup) -> None:
        """Only LUM's own snapshots (lum_*) and backups (note) of the guests LUM manages;
        the host script enforces that as well. One at a time, like the Delete buttons."""
        items: list[tuple[str, int, str | int]] = []
        try:
            for c in self.db.containers():
                vmid, label = c["vmid"], f"{'VM' if c['type'] == 'qemu' else 'CT'} {c['vmid']}"
                try:
                    if cl.snapshots:
                        items += [("snapshot", vmid, s["name"]) for s in await self.host.snapshots(vmid)]
                    if cl.backups:
                        for b in await self.host.backups(vmid):
                            if b["protected"]:
                                cl.skipped.append(f"{label}: backup of {time.strftime('%Y-%m-%d %H:%M', time.localtime(b['ctime']))} is protected")
                            else:
                                items.append(("backup", vmid, b["id"]))
                except Exception as err:
                    cl.failed.append(f"{label}: cannot list: {err}")
            cl.total, cl.state = len(items), "deleting"
            for kind, vmid, ref in items:
                try:
                    if kind == "snapshot":
                        await self.delete_snapshot(vmid, str(ref))
                        cl.deleted_snapshots += 1
                    else:
                        await self.delete_backup(vmid, int(ref))
                        cl.deleted_backups += 1
                except RuntimeError as err:  # a job runs on that guest
                    cl.skipped.append(f"{vmid}: {kind} {ref}: {err}")
                except Exception as err:
                    cl.failed.append(f"{vmid}: {kind} {ref}: {err}")
                cl.done += 1
        finally:
            cl.state, cl.finished = "done", time.time()
            log.info("cleanup: %s snapshots and %s backups deleted, %s skipped, %s failed",
                     cl.deleted_snapshots, cl.deleted_backups, len(cl.skipped), len(cl.failed))

    # --- queue: several guests, one after the other -------------------------------

    @property
    def queue_active(self) -> bool:
        return any(i.state in ("waiting", "running") for i in self.queue)

    def enqueue(self, vmids: list[int], kind: str, backup: bool, cleanup: bool = True) -> int:
        """Add guests to the queue (once each); returns how many were added."""
        if not self.queue_active:
            self.queue.clear()  # a new run: drop the results of the last one
        queued = {i.vmid for i in self.queue if i.state in ("waiting", "running")}
        added = 0
        for vmid in dict.fromkeys(vmids):
            if vmid not in queued:
                self.queue.append(QueueItem(vmid, kind, backup, cleanup))
                added += 1
        if added and (self._queue_task is None or self._queue_task.done()):
            self._queue_task = asyncio.create_task(self._run_queue())
        return added

    def cancel_queue(self) -> None:
        """Waiting guests are cancelled (a running update finishes); an idle queue is cleared."""
        if not self.queue_active:
            self.queue.clear()
        for i in self.queue:
            if i.state == "waiting":
                i.state = "cancelled"

    def _skip_reason(self, item: QueueItem) -> str | None:
        c = self.db.container(item.vmid)
        if not c:
            return "no longer exists"
        if c["status"] != "running":
            return "not running"
        if item.kind == "os":
            return None if c["upgradable"] else "no OS updates"
        if c["type"] == "qemu":
            return "app updates are only supported for containers"
        if c["self_created"]:
            return "tagged self-created"
        if not c["community_script"]:
            return "no community-scripts app"
        if c["app_kind"] == "os":
            return "the app comes with the OS updates"
        if c["app_installed"] and c["app_latest"] and not c["app_update"]:
            return "app is up to date"
        return None

    async def _run_queue(self) -> None:
        while item := next((i for i in self.queue if i.state == "waiting"), None):
            # a delete or a single update on this guest may still run: wait for it
            while item.vmid in self._busy and item.state == "waiting":
                await asyncio.sleep(2)
            if item.state != "waiting":  # cancelled meanwhile
                continue
            reason = self._skip_reason(item)
            if reason:
                item.state, item.note = "skipped", reason
                continue
            try:
                job = self.start_job(item.vmid, item.kind, backup=item.backup, cleanup=item.cleanup)
            except RuntimeError as err:
                item.state, item.note = "failed", str(err)
                continue
            item.state, item.job_id = "running", job.id
            await job.wait()
            item.state = "ok" if job.success else "failed"
            if not job.success:
                # the reason: "### Error: ..." or "### skipped: ..." (the community script
                # refused, e.g. too little RAM - nothing was changed)
                reasons = [line[4:] for line in job.lines
                           if line.startswith(("### Error", "### Internal error", "### skipped"))]
                item.note = reasons[-1] if reasons else "see log"
                if item.note.startswith("skipped"):
                    item.state = "skipped"


async def status_poller(service: UpdateService, seconds: int = 60) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            await service.poll_status()
        except Exception as err:  # host unreachable: the next "Check all" reports it
            log.debug("status poll failed: %s", err)


async def scheduler(service: UpdateService, interval_minutes: int) -> None:
    while True:
        try:
            await service.refresh_all()
        except Exception:
            log.exception("scheduled refresh failed")
        await asyncio.sleep(interval_minutes * 60)
