"""Update checks and update jobs."""

import asyncio
import itertools
import json
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime

from .apps import AppCatalog, normalize
from .config import Settings
from . import schedule
from .db import Database, is_security
from .host import HostCommandError
from .i18n import LumError, LumValueError, t
from .notify import Notifier, esc, lines

log = logging.getLogger(__name__)

EXCLUDE_TAG = "no-lum"  # Proxmox tag: LUM leaves this container / VM alone
QUEUE_CLEAR_AFTER = 15  # seconds a queue without failures stays visible after it finished
JOB_KEEP = 3600  # seconds a finished job stays in memory; its log is in the history
JOB_LINKS = 5000  # job id -> history entry of removed jobs (for the queue's Log button)
HOST_CHECK_WRAPPER = 12  # host script version with "host-check" (updates of the Proxmox host)

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
    auto: bool = False  # started by the maintenance window
    queued: bool = False  # part of a bulk / automatic update: reported in its summary
    lines: list[str] = field(default_factory=list)
    done: bool = False
    success: bool | None = None
    finished: float | None = None  # when it ended (finished jobs leave memory after JOB_KEEP)
    history_id: int | None = None
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
        self.done, self.success, self.finished = True, success, time.time()
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


# app update output meaning "this script doesn't update the app" (it exits with 0)
NO_APP_UPDATE = re.compile(
    r"built-in updater|updates itself automatically|don't provide an update function|to update use the \S+ manager"
    r"|no update function provided|should be updated via|use the applications? web interface"
    r"|is updated via web interface|create a new container and transfer",
    re.I,
)


@dataclass
class QueueItem:
    """One guest in the update queue (several guests updated one after the other)."""
    vmid: int
    kind: str  # "os" | "app" | "restart"
    backup: bool
    cleanup: bool = True
    auto: bool = False  # maintenance window
    state: str = "waiting"  # waiting | running | ok | failed | skipped | cancelled
    job_id: int | None = None
    note: str = ""  # English (log); the web UI and Telegram translate note_key
    note_key: str | None = None
    note_params: dict = field(default_factory=dict)

    def set_note(self, key: str, **params) -> None:
        self.note_key, self.note_params, self.note = key, params, t(key, "en", **params)

    def note_text(self, lang: str) -> str:
        return t(self.note_key, lang, **self.note_params) if self.note_key else self.note

    def as_dict(self) -> dict:
        return {"vmid": self.vmid, "kind": self.kind, "backup": self.backup, "cleanup": self.cleanup,
                "auto": self.auto, "state": self.state, "job_id": self.job_id,
                "note": self.note, "note_key": self.note_key, "note_params": self.note_params}


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
    def __init__(self, settings: Settings, db: Database, host, catalog: AppCatalog,
                 notifier: Notifier | None = None):
        self._s = settings
        self.db = db
        self.host = host
        self.catalog = catalog
        self.jobs: dict[int, Job] = {}
        self._job_ids = itertools.count(1)
        self.job_history: dict[int, int] = {}  # removed jobs: id -> history entry
        self._busy: set[int] = set()  # vmids with a running job
        self._check_sem = asyncio.Semaphore(settings.max_parallel_checks)
        # the last "Check all" - stored, so a restart doesn't trigger a new one
        stored = db.get_meta("last_refresh")
        self.last_refresh: float | None = float(stored) if stored else None
        self.wrapper_version: int | None = None  # host script version, None = unknown
        self.nodes: list[dict] | None = None  # cluster nodes with their wrapper version
        # updates of the Proxmox host(s) themselves, from the last "Check all" (stored)
        self.hosts: list[dict] = json.loads(db.get_meta("hosts") or "[]")
        self.hidden: list[int] = []  # guests tagged EXCLUDE_TAG
        self.refreshing = False
        self.checking: set[int] = set()  # guests being checked right now
        # "Check all" in progress: {"started", "total", "done"}; the last one: {"finished", "total", "seconds"}
        self.refresh_progress: dict | None = None
        self.refresh_done: dict | None = None
        self.queue: list[QueueItem] = []
        self._queue_task: asyncio.Task | None = None
        self.cleanup: Cleanup | None = None
        self.notifier = notifier or Notifier(settings)
        # every update the last "updates available" message reported (stored: a restart
        # of LUM doesn't send the same message again)
        self._updates_reported: set[str] = set(json.loads(db.get_meta("updates_reported") or "[]"))
        # maintenance window: the run in progress, the date of the last window, a summary
        self._maint_items: list[QueueItem] | None = None
        self._maint_end: datetime | None = None
        self._last_window: datetime | None = None
        self.maintenance_last: dict | None = None
        self.self_vmid: int | None = None  # the container LUM runs in, if it is on this host
        self._find_self([{"vmid": c["vmid"], "name": c["name"]} for c in db.containers()])
        self._close_interrupted()

    # --- notifications ------------------------------------------------------------------

    def _guest(self, vmid: int) -> str:
        c = self.db.container(vmid) or {}
        label = f"{'VM' if c.get('type') == 'qemu' else 'CT'} {vmid}"
        return f"{label} {c['name']}" if c.get("name") else label

    @staticmethod
    def _outcome(job: Job) -> tuple[str, str]:
        """(state, reason) of a finished job: ok | failed | skipped"""
        reasons = [line[4:] for line in job.lines if line.startswith(("### Error", "### Internal error", "### skipped"))]
        reason = reasons[-1] if reasons else ""
        if job.success:
            return "ok", ""
        return ("skipped" if reason.startswith("skipped") else "failed"), reason

    async def _notify_job(self, job: Job) -> None:
        state, reason = self._outcome(job)
        if not self.notifier.wants(state == "failed"):
            return
        lang = self.notifier.lang
        icon = {"ok": "✅", "failed": "❌", "skipped": "⏭"}[state]
        text = f"{icon} <b>LUM</b>: " + t(f"tg.job_{state}", lang, kind=t(f"kind.{job.kind}", lang),
                                        guest=esc(self._guest(job.vmid)))
        if reason:
            text += f"\n{esc(reason)}"
        await self.notifier.send(text)

    async def _notify_queue(self, items: list["QueueItem"]) -> None:
        count = {s: [i for i in items if i.state == s] for s in ("ok", "failed", "skipped", "cancelled")}
        if not items or not self.notifier.wants(bool(count["failed"])):
            return
        lang = self.notifier.lang
        title = t("tg.title_auto" if all(i.auto for i in items)
                  else "tg.title_restart" if all(i.kind == "restart" for i in items) else "tg.title_bulk", lang)
        text = (f"<b>LUM – {t('tg.queue_finished', lang, title=title)}</b>\n"
                + t("tg.queue_counts", lang, ok=len(count["ok"]), failed=len(count["failed"]),
                    skipped=len(count["skipped"]) + len(count["cancelled"])))
        if count["failed"]:
            text += "\n\n" + lines([f"{esc(self._guest(i.vmid))}: {t(f'kind.{i.kind}', lang)} – {esc(i.note_text(lang))}"
                                     for i in count["failed"]], lang)
        await self.notifier.send(text)

    async def notify_updates(self) -> None:
        """After the scheduled check: which guests have updates. Sent only when something
        is new since the last message - a new package (version), app version, restart
        required; updates that were installed meanwhile don't trigger a message."""
        if not (self.notifier.enabled and self._s.notify_updates):
            return
        lang = self.notifier.lang
        found, current = [], set()
        unseen: list[str] = []  # stopped guests, hosts whose check failed: keep what was reported

        def line(label: str, parts: list[str], items: set[str]) -> None:
            current.update(items)
            new = "🆕 " if items - self._updates_reported else ""  # what this message adds
            found.append(f"{new}{esc(label)}: {esc(', '.join(parts))}")

        for c in self.db.containers():
            if c["status"] != "running":
                unseen.append(f"{c['vmid']}:")
                continue
            parts = []
            if c["upgradable"]:
                sec = f" ({t('tg.security', lang, count=len(c['security']))})" if c["security"] else ""
                parts.append(t("tg.packages", lang, count=len(c["upgradable"])) + sec)
            if c["app_update"]:
                parts.append(t("tg.app", lang, installed=c["app_installed"], latest=c["app_latest"]))
            if c["restart_required"]:
                parts.append(t("tg.restart_required", lang))
            if parts:
                v = c["vmid"]
                line(self._guest(v), parts, {f"{v}:pkg:{p}" for p in c["upgradable"]}
                     | ({f"{v}:app:{c['app_latest']}"} if c["app_update"] else set())
                     | ({f"{v}:restart"} if c["restart_required"] else set()))
        for h in self.hosts:
            if h.get("error"):
                unseen.append(f"host:{h['node']}:")
                continue
            if not (h.get("packages") or h.get("reboot")):
                continue
            parts = []
            if h.get("packages"):
                sec = f" ({t('tg.security', lang, count=len(h['security']))})" if h.get("security") else ""
                parts.append(t("tg.packages", lang, count=len(h["packages"])) + sec)
            if h.get("reboot"):
                parts.append(t("tg.reboot_required", lang))
            n = h["node"]
            line(t("tg.host", lang, node=n), parts, {f"host:{n}:pkg:{p}" for p in h.get("packages") or []}
                 | ({f"host:{n}:reboot"} if h.get("reboot") else set()))
        new = current - self._updates_reported
        # a guest that wasn't looked at this time (stopped) keeps what was reported, so its
        # updates don't count as new when it runs again
        current |= {i for i in self._updates_reported if i.startswith(tuple(unseen))} if unseen else set()
        # remember the current state either way: what was installed meanwhile is dropped,
        # so it counts as new again should it come back
        self._updates_reported = current
        self.db.set_meta("updates_reported", json.dumps(sorted(current)))
        if new:
            await self.notifier.send(f"🔔 <b>LUM – {t('tg.updates', lang)}</b>\n{lines(found, lang)}")

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
        progress = self.refresh_progress = {"started": time.time(), "total": None, "done": 0}
        try:
            try:
                self.wrapper_version = await self.host.wrapper_version()
            except (OSError, ValueError) as err:
                log.warning("cannot read host script version: %s", err)
            await self._read_nodes()
            containers = self._managed(await self.host.list_containers())
            self._apply_list(containers)
            running = [int(c["vmid"]) for c in containers
                       if c.get("status") == "running" and int(c["vmid"]) not in self._busy]
            progress["total"] = len(running)
            self.checking.update(running)  # all of them show "checking" until their turn is done

            async def one(vmid: int) -> None:
                await self.check(vmid)
                progress["done"] += 1

            stopped = [int(c["vmid"]) for c in containers
                        if int(c["vmid"]) not in running and int(c["vmid"]) not in self._busy]
            await asyncio.gather(*(one(vmid) for vmid in running),
                                 *(self.count_restore_points(vmid) for vmid in stopped))
            self.last_refresh = time.time()
            self.db.set_meta("last_refresh", str(self.last_refresh))
            self.refresh_done = {"finished": self.last_refresh, "total": len(running),
                                 "seconds": round(self.last_refresh - progress["started"])}
            log.info("check all: %s guests in %s s", len(running), self.refresh_done["seconds"])
            await self.check_hosts()
        finally:
            self.refreshing = False
            self.refresh_progress = None

    async def check_hosts(self) -> None:
        """Pending updates of every Proxmox node (read only; needs host script 12 there)."""
        nodes = self.nodes
        if not nodes:
            # node list unknown: at least the host LUM talks to (the wrapper's own node)
            if (self.wrapper_version or 0) < HOST_CHECK_WRAPPER:
                log.info("host check skipped: needs host script %s (have %s)", HOST_CHECK_WRAPPER, self.wrapper_version)
                return
            nodes = [{"node": "", "online": 1, "wrapper": self.wrapper_version}]
        before = {h["node"]: h for h in self.hosts}
        result = []
        for n in nodes:
            name = n["node"]
            entry = {**before.get(name, {}), "node": name or "Proxmox host", "error": None,
                     "error_key": None, "error_params": {}}
            if not n["online"]:
                entry.update(error=t("host.offline", "en"), error_key="host.offline")
            elif n["wrapper"] < HOST_CHECK_WRAPPER:
                entry.update(error=t("host.old_wrapper", "en", version=HOST_CHECK_WRAPPER), error_key="host.old_wrapper",
                             error_params={"version": HOST_CHECK_WRAPPER})
            else:
                try:
                    data = await self.host.host_check(name)
                    entry["node"] = name or data.get("node") or entry["node"]
                    packages = data.get("packages") or []
                    newest, kernel = data.get("newest_kernel") or "", data.get("kernel") or ""
                    entry.update(
                        pve=data.get("pve") or "", kernel=kernel, newest_kernel=newest,
                        packages=packages, security=[p for p in packages if is_security(p)],
                        reboot=bool(data.get("reboot_flag")) or bool(newest and newest != kernel),
                        lists=data.get("lists") or None, checked=time.time(),
                    )
                except Exception as err:  # one node must not hide the others
                    log.warning("host check of node %s failed: %s", name or "(local)", err)
                    entry["error"] = str(err) or type(err).__name__
            result.append(entry)
        self.hosts = result
        self.db.set_meta("hosts", json.dumps(result))
        log.info("host check: %s", ", ".join(
            f"{h['node']}: {h['error'] or str(len(h.get('packages') or [])) + ' updates'}" for h in result) or "no nodes")

    async def _read_nodes(self) -> None:
        """Cluster nodes and their wrapper; unknown with host scripts < 11."""
        try:
            self.nodes = await self.host.nodes()
        except (HostCommandError, OSError, ValueError) as err:
            log.debug("cannot read the nodes: %s", err)
            self.nodes = None

    async def sync_guests(self) -> dict:
        """Re-read only the list of containers/VMs from the host - no package checks.
        New running guests get checked in the background so they show their state."""
        guests = self._managed(await self.host.list_containers())
        await self._read_nodes()
        added, removed, restarted = self._apply_list(guests)
        for g in guests:
            vmid = int(g["vmid"])
            if vmid in added + restarted and g.get("status") == "running" and vmid not in self._busy:
                asyncio.create_task(self.check(vmid))
        asyncio.create_task(self.check_hosts())  # also after a start (startup_sync)
        return {"added": added, "removed": removed, "hidden": self.hidden}

    async def check(self, vmid: int) -> None:
        self.checking.add(vmid)
        try:
            await self._check(vmid)
        finally:
            self.checking.discard(vmid)

    async def _check(self, vmid: int) -> None:
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
            await self.count_restore_points(vmid)
            if info.script and not (self.db.container(vmid) or {}).get("no_app"):
                await self._check_app(vmid, info.script)

    async def _check_disk(self, vmid: int) -> None:
        """Free space in the guest for the "low disk" hint; unknown with host scripts < 8."""
        try:
            sp = await self.host.space(vmid)
            self.db.set_disk(vmid, sp.get("guest_avail_kb"), sp.get("guest_size_kb"))
        except (HostCommandError, OSError, ValueError) as err:
            log.debug("disk check %s: %s", vmid, err)
            self.db.set_disk(vmid, None, None)

    async def count_restore_points(self, vmid: int) -> None:
        """How many snapshots / vzdump backups LUM made of the guest (the Backups button is
        greyed out without any); unknown (button usable) if either list can't be read."""
        try:
            count = len(await self.host.snapshots(vmid)) + len(await self.host.backups(vmid))
        except (HostCommandError, OSError, ValueError) as err:
            log.debug("cannot count snapshots/backups of %s: %s", vmid, err)
            count = None
        self.db.set_restore_points(vmid, count)

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
        note = src.hint or None
        if src.pinned:
            note = "held back by the community script" + (f": {src.pin_reason}" if src.pin_reason else "")
        self.db.set_app_result(vmid, script, src.label or None, normalize(installed or "") or None, latest,
                               src.kind, src.url or None, note)

    # --- jobs --------------------------------------------------------------

    def start_job(self, vmid: int, kind: str, target: str | None = None, backup: bool = True,
                  cleanup: bool = True, auto: bool = False) -> Job:
        """kind: "os" | "app" update, or "rollback" to the lum_ snapshot <target>.
        backup=False skips the safety copy for this one update, cleanup=False the
        apt autoremove / clean after an OS update."""
        if vmid in self._busy:
            raise LumError("err.guest_busy", vmid=vmid)
        job = Job(id=next(self._job_ids), vmid=vmid, kind=kind, target=target, backup=backup, cleanup=cleanup,
                  auto=auto)
        self.jobs[job.id] = job
        self._busy.add(vmid)
        run = {"rollback": self._run_rollback, "restore": self._run_restore,
               "restart": self._run_restart}.get(kind, self._run_update)
        asyncio.create_task(self._guarded(job, run))
        return job

    async def _guarded(self, job: Job, run) -> None:
        history_id = job.history_id = self.db.start_history(job.vmid, job.kind, job.target, job.auto)
        log.info("job %s: %s of %s started%s", job.id, job.kind, job.vmid, " (auto)" if job.auto else "")
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
            state, reason = self._outcome(job)
            (log.info if state != "failed" else log.warning)(
                "job %s: %s of %s %s%s", job.id, job.kind, job.vmid,
                {"ok": "succeeded", "failed": "failed", "skipped": "skipped"}[state], f" - {reason}" if reason else "")
        if not job.queued:
            asyncio.create_task(self._notify_job(job))
        await self.check(job.vmid)
        if job.kind == "restore":
            await self.count_restore_points(job.vmid)
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
        no_update = None
        async for line in stream:
            job.emit(line)
            if job.kind == "app" and NO_APP_UPDATE.search(line):
                no_update = line.strip()
        if no_update:
            # exit 0, but nothing was updated: don't call it a success
            job.emit(f"### skipped: the community script doesn't update this app - {no_update}")
            if job.backup:
                await self._prune(job)  # nothing changed, the copy may rotate
            return False

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
            raise LumError("err.guest_busy", vmid=vmid)
        self._busy.add(vmid)
        try:
            await self.host.delete_snapshot(vmid, name)
            self.db.mark_snapshots_removed(vmid, [name])
            self.db.restore_point_removed(vmid)
        finally:
            self._busy.discard(vmid)

    async def delete_backup(self, vmid: int, backup_id: int) -> None:
        """Delete one vzdump backup made by LUM; blocks the guest like a job meanwhile."""
        if vmid in self._busy:
            raise LumError("err.guest_busy", vmid=vmid)
        self._busy.add(vmid)
        try:
            await self.host.delete_backup(vmid, backup_id)
            self.db.mark_backup_removed(vmid, backup_id)
            self.db.restore_point_removed(vmid)
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
            raise LumValueError("err.backup_ambiguous")
        if found[0]["protected"]:
            raise LumValueError("err.backup_protected")
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

    def next_refresh(self, interval_minutes: int) -> float:
        return (self.last_refresh or 0) + interval_minutes * 60

    async def startup_sync(self) -> None:
        """After a start: host script version, nodes and the guest list (new running guests
        get checked) - but no "Check all" until its interval is over."""
        try:
            self.wrapper_version = await self.host.wrapper_version()
        except (OSError, ValueError) as err:
            log.warning("cannot read host script version: %s", err)
        await self.sync_guests()

    def rotate(self) -> None:
        s = self._s
        removed, logs = self.db.rotate(s.history_days, s.history_max, s.log_days)
        if removed or logs:
            log.info("history rotation: %s entries and %s logs removed", removed, logs)

    def prune_jobs(self, keep: float = JOB_KEEP) -> int:
        """Drop finished jobs from memory after an hour - otherwise every job and its
        whole log would stay until LUM restarts. Their logs are in the history; the job
        id keeps pointing there (the queue's Log button)."""
        cutoff = time.time() - keep
        old = [j for j in self.jobs.values() if j.done and j.finished and j.finished < cutoff]
        for j in old:
            del self.jobs[j.id]
            if j.history_id:
                self.job_history[j.id] = j.history_id
        while len(self.job_history) > JOB_LINKS:  # oldest first (dicts keep insertion order)
            del self.job_history[next(iter(self.job_history))]
        if old:
            log.info("%s finished jobs removed from memory", len(old))
        return len(old)

    def busy(self, vmid: int) -> bool:
        return vmid in self._busy

    # --- delete all of LUM's snapshots / backups ------------------------------------

    @property
    def cleanup_active(self) -> bool:
        return self.cleanup is not None and self.cleanup.state != "done"

    def start_cleanup(self, snapshots: bool, backups: bool) -> Cleanup:
        if self.cleanup_active:
            raise LumError("err.cleanup_running")
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

    # --- maintenance window: automatic updates -----------------------------------------

    def _window(self) -> tuple:
        """(weekdays, start time, end time or None) from the settings"""
        s = self._s
        return (schedule.parse_days(s.auto_days), schedule.parse_time(s.auto_time),
                schedule.parse_time(s.auto_until) if s.auto_until else None)

    @property
    def maintenance_running(self) -> bool:
        return self._maint_items is not None

    def maintenance_status(self) -> dict:
        days, start, until = self._window()
        nxt = schedule.next_start(days, start, datetime.now()) if days else None
        return {
            "days": schedule.describe(days) if days else "", "time": self._s.auto_time,
            "until": self._s.auto_until, "restart": self._s.auto_restart,
            "next": nxt.timestamp() if nxt else None, "running": self.maintenance_running,
            "guests": sum(1 for c in self.db.containers() if c["auto_update"] in ("os", "all")),
            "last": self.maintenance_last,
        }

    async def maintenance_tick(self, now: datetime | None = None) -> None:
        """Called every few seconds: start the window, or end it."""
        days, start, until = self._window()
        now = now or datetime.now()
        if self.maintenance_running:
            if self._maint_end and now >= self._maint_end:
                for i in self._maint_items or []:
                    if i.state == "waiting":
                        i.state = "cancelled"
                        i.set_note("skip.window_ended")
            return
        begin = schedule.window_start(days, start, now) if days else None
        if begin and begin != self._last_window:
            self._last_window = begin
            self.start_maintenance(schedule.window_end(begin, until))

    def start_maintenance(self, end: datetime | None = None) -> None:
        if self.maintenance_running:
            raise LumError("err.auto_running")
        self._maint_items, self._maint_end = [], end
        asyncio.create_task(self._run_maintenance())

    async def _run_maintenance(self) -> None:
        """Check everything, then queue the updates (and restarts) of the guests set to
        auto-update - like a bulk update, marked "auto" in the history."""
        started = time.time()
        log.info("maintenance window: automatic updates start")
        try:
            await self.refresh_all()
            guests = [c for c in self.db.containers()
                      if c["auto_update"] in ("os", "all") and c["status"] == "running"]
            items = [QueueItem(c["vmid"], "os", True, self._s.cleanup, auto=True) for c in guests]
            items += [QueueItem(c["vmid"], "app", True, auto=True) for c in guests if c["auto_update"] == "all"]
            if self._s.auto_restart:
                items += [QueueItem(c["vmid"], "restart", False, auto=True) for c in guests]
            self._maint_items = self._enqueue_items(items)
            while any(i.state in ("waiting", "running") for i in self._maint_items):
                await asyncio.sleep(5)
            count = {s: sum(1 for i in self._maint_items if i.state == s)
                     for s in ("ok", "failed", "skipped", "cancelled")}
            self.maintenance_last = {"started": started, "finished": time.time(), "guests": len(guests), **count}
            log.info("maintenance window done: %s", self.maintenance_last)
        except Exception as err:
            log.exception("maintenance window failed")
            self.maintenance_last = {"started": started, "finished": time.time(), "error": str(err)}
        finally:
            self._maint_items, self._maint_end = None, None

    # --- queue: several guests, one after the other -------------------------------

    @property
    def queue_active(self) -> bool:
        return any(i.state in ("waiting", "running") for i in self.queue)

    def enqueue(self, vmids: list[int], kind: str, backup: bool, cleanup: bool = True) -> int:
        """Add guests to the queue (once each); returns how many were added."""
        return len(self._enqueue_items([QueueItem(v, kind, backup, cleanup) for v in dict.fromkeys(vmids)]))

    def _enqueue_items(self, items: list[QueueItem]) -> list[QueueItem]:
        """Append items not already waiting / running (same guest and kind); returns them."""
        if not self.queue_active:
            self.queue.clear()  # a new run: drop the results of the last one
        queued = {(i.vmid, i.kind) for i in self.queue if i.state in ("waiting", "running")}
        added = [i for i in items if (i.vmid, i.kind) not in queued]
        self.queue.extend(added)
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
            return "gone"
        if c["status"] != "running":
            return "not_running"
        if item.kind == "os":
            return None if c["upgradable"] else "no_os_updates"
        if item.kind == "restart":
            if c["vmid"] == self.self_vmid:
                return "self"
            # the maintenance window only restarts guests that need it; a bulk restart all chosen
            return None if c["restart_required"] or not item.auto else "no_restart_needed"
        if c["type"] == "qemu":
            return "vm"
        if c["no_app"]:
            return "no_app"
        if not c["community_script"]:
            return "no_community"
        if c["app_kind"] == "os":
            return "app_os"
        if c["app_kind"] == "builtin":
            return "app_builtin"
        if c["app_installed"] and c["app_latest"] and not c["app_update"]:
            return "app_up_to_date"
        return None

    async def _run_queue(self) -> None:
        while item := next((i for i in self.queue if i.state == "waiting"), None):
            if item.kind == "restart" and not item.auto:
                # a bulk restart: all its guests at the same time (the window's restarts
                # come after the updates, one after the other)
                batch = [i for i in self.queue if i.state == "waiting" and i.kind == "restart" and not i.auto]
                await asyncio.gather(*(self._run_item(i) for i in batch))
            else:
                await self._run_item(item)
        # drained: one message for the whole run (instead of one per guest)
        try:
            await self._notify_queue(list(self.queue))
        except Exception:
            log.exception("queue notification failed")
        if not any(i.state == "failed" for i in self.queue):
            asyncio.create_task(self._clear_queue_later(list(self.queue)))

    async def _run_item(self, item: QueueItem) -> None:
        # a delete or a single update on this guest may still run: wait for it
        while item.vmid in self._busy and item.state == "waiting":
            await asyncio.sleep(2)
        if item.state != "waiting":  # cancelled meanwhile
            return
        if item.kind == "restart":  # the updates before may just have changed it
            await self._check_restart(item.vmid)
        reason = self._skip_reason(item)
        if reason:
            item.state = "skipped"
            item.set_note(f"skip.{reason}")
            return
        try:
            job = self.start_job(item.vmid, item.kind, backup=item.backup, cleanup=item.cleanup, auto=item.auto)
        except LumError as err:
            item.state = "failed"
            item.set_note(err.key, **err.params)
            return
        except RuntimeError as err:
            item.state, item.note = "failed", str(err)
            return
        item.state, item.job_id = "running", job.id
        job.queued = True
        await job.wait()
        item.state = "ok" if job.success else "failed"
        if not job.success:
            # the reason: "### Error: ..." or "### skipped: ..." (the community script
            # refused, e.g. too little RAM - nothing was changed)
            reasons = [line[4:] for line in job.lines
                       if line.startswith(("### Error", "### Internal error", "### skipped"))]
            if reasons:
                item.note = reasons[-1]  # from the log: stays English like the log
            else:
                item.set_note("skip.see_log")
            if item.note.startswith("skipped"):
                item.state = "skipped"

    async def _clear_queue_later(self, finished: list[QueueItem]) -> None:
        """Nothing failed: the queue disappears after a moment (the results are in the
        history); with a failure it stays until "Clear"."""
        await asyncio.sleep(QUEUE_CLEAR_AFTER)
        same = len(self.queue) == len(finished) and all(a is b for a, b in zip(self.queue, finished))
        if same and not self.queue_active:  # no new run meanwhile
            self.queue.clear()


async def housekeeping(service: UpdateService, seconds: int = 3600) -> None:
    """Right after the start, then every hour: history and log rotation, finished jobs
    out of memory, and give unused space in the database file back."""
    while True:
        for name, step in (("history rotation", service.rotate), ("job cleanup", service.prune_jobs),
                           ("database compaction", service.db.compact)):
            try:
                step()
            except Exception:
                log.exception("%s failed", name)
        await asyncio.sleep(seconds)


async def maintenance_loop(service: UpdateService, seconds: int = 20) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            await service.maintenance_tick()
        except Exception:
            log.exception("maintenance window failed")


async def status_poller(service: UpdateService, seconds: int = 60) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            await service.poll_status()
        except Exception as err:  # host unreachable: the next "Check all" reports it
            log.debug("status poll failed: %s", err)


async def scheduler(service: UpdateService, interval_minutes: int) -> None:
    """"Check all" every interval, counted from the last one - also across restarts and
    after a manual "Check all"."""
    try:
        await service.startup_sync()
    except Exception as err:
        log.warning("first look at the host failed: %s", err)
    while True:
        wait = service.next_refresh(interval_minutes) - time.time()
        if wait > 0:
            await asyncio.sleep(min(wait, 60))  # look again: a manual check moves the time
            continue
        try:
            await service.refresh_all()
            await service.notify_updates()
        except Exception:
            log.exception("scheduled refresh failed")
            await asyncio.sleep(60)  # host down: don't retry in a tight loop
