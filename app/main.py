import asyncio
import logging
import math
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import REQUIRED_WRAPPER_VERSION, __version__, settings_edit
from .apps import AppCatalog
from .auth import COOKIE, Auth, write_credentials
from .config import get_settings
from .db import Database
from .host import HostClient
from .service import UpdateService, maintenance_loop, scheduler, status_poller

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
# reachable without a session
PUBLIC = {
    "/login", "/api/login", "/api/auth/state", "/favicon.ico",
    "/static/style.css", "/static/login.js", "/static/theme.js",
    "/static/logo.svg", "/static/favicon.svg", "/static/favicon-32.png", "/static/apple-touch-icon.png",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        settings = get_settings()
    except ValidationError as err:
        raise RuntimeError(f"invalid configuration: {err}") from None
    catalog = AppCatalog(settings.github_token)
    if settings.demo:
        from .demo import DemoHostClient

        host = DemoHostClient(catalog)
    else:
        if not settings.pve_host:
            raise RuntimeError("LUM_PVE_HOST is not set (or set LUM_DEMO=true)")
        host = HostClient(settings)

    app.state.auth = Auth(settings.auth_file, settings.secret_file, settings.session_hours)
    if settings.demo and not app.state.auth.configured:
        write_credentials(settings.auth_file, "admin", "demo")
        log.warning("demo mode: created login admin / demo")
    if settings.auth_disabled:
        log.warning("LUM_AUTH_DISABLED=true - the web UI is open to everyone who can reach it")
    elif not app.state.auth.configured:
        log.warning("no login configured - set one with: venv/bin/python -m app.passwd")

    app.state.service = UpdateService(
        settings, Database(settings.db_path), host, catalog
    )
    tasks = [asyncio.create_task(scheduler(app.state.service, settings.check_interval_minutes)),
             asyncio.create_task(status_poller(app.state.service)),
             asyncio.create_task(maintenance_loop(app.state.service))]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="LXC Update Manager", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


_ASSET = re.compile(r'((?:href|src)="/static/[\w.-]+\.(?:css|js|svg|png))"')


def _page(name: str) -> HTMLResponse:
    """Serve a page with ?v=<version> on its CSS/JS/icon links. A new version means new
    URLs, so no browser or proxy cache (e.g. "Cache Assets" in Nginx Proxy Manager)
    can mix a new page with an old style.css or app.js."""
    html = (STATIC / name).read_text(encoding="utf-8")
    return HTMLResponse(_ASSET.sub(lambda m: f'{m.group(1)}?v={__version__}"', html))


def svc() -> UpdateService:
    return app.state.service


def auth() -> Auth:
    return app.state.auth


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def no_stale_ui(request: Request, call_next):
    """Without a Cache-Control header browsers may keep the old app.js after an
    update. no-cache still allows caching, but revalidates (cheap 304) every time."""
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/login") or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    # CSRF: other sites can't set custom headers on cross-site requests
    if request.method not in ("GET", "HEAD", "OPTIONS") and path.startswith("/api/"):
        if request.headers.get("x-requested-with") != "lum":
            return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
    if get_settings().auth_disabled or path in PUBLIC:
        return await call_next(request)
    user = auth().check_token(request.cookies.get(COOKIE))
    if not user:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "not logged in"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    request.state.user = user
    return await call_next(request)


def _set_session(response: JSONResponse, username: str, request: Request) -> None:
    # behind an HTTPS reverse proxy (trusted via FORWARDED_ALLOW_IPS) the scheme is
    # https, so the cookie is marked Secure without extra configuration
    secure = get_settings().cookie_secure or request.url.scheme == "https"
    response.set_cookie(
        COOKIE,
        auth().make_token(username),
        max_age=auth().session_seconds,
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )


def _check_lock(ip: str) -> None:
    if wait := auth().locked_for(ip):
        raise HTTPException(429, f"Too many failed attempts. Try again in {math.ceil(wait / 60)} minutes.")


class LoginBody(BaseModel):
    username: str
    password: str


class PasswordBody(BaseModel):
    current: str
    new: str


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # browsers and bookmarks ask for /favicon.ico regardless of the <link> tags
    return FileResponse(STATIC / "favicon-32.png", media_type="image/png")


@app.get("/login")
async def login_page(request: Request):
    if get_settings().auth_disabled or auth().check_token(request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=303)
    return _page("login.html")


@app.get("/api/auth/state")
async def auth_state():
    cfg = get_settings()
    return {"enabled": not cfg.auth_disabled, "configured": auth().configured, "demo": cfg.demo}


@app.post("/api/login")
async def login(body: LoginBody, request: Request):
    ip = client_ip(request)
    _check_lock(ip)
    if not auth().configured:
        raise HTTPException(503, "No login set up. Run inside the container: "
                                 "cd /opt/lxc-update-manager && venv/bin/python -m app.passwd")
    if not await asyncio.to_thread(auth().check_password, body.username, body.password):
        auth().record_failure(ip)
        log.warning("failed login for %r from %s", body.username, ip)
        await asyncio.sleep(1)
        raise HTTPException(401, "Wrong username or password")
    auth().reset_failures(ip)
    response = JSONResponse({"user": body.username})
    _set_session(response, body.username, request)
    return response


@app.post("/api/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


@app.get("/api/me")
async def me(request: Request):
    return {"user": getattr(request.state, "user", None), "auth_enabled": not get_settings().auth_disabled}


@app.post("/api/password")
async def change_password(body: PasswordBody, request: Request):
    if get_settings().auth_disabled:
        raise HTTPException(409, "Login is disabled (LUM_AUTH_DISABLED)")
    ip, user = client_ip(request), request.state.user
    _check_lock(ip)
    # 403, not 401: the session itself is fine, only the entered password is wrong
    if not await asyncio.to_thread(auth().check_password, user, body.current):
        auth().record_failure(ip)
        await asyncio.sleep(1)
        raise HTTPException(403, "Current password is wrong")
    if len(body.new) < 8:
        raise HTTPException(400, "The new password needs at least 8 characters")
    await asyncio.to_thread(auth().change_password, body.new)
    # all old sessions are invalid now; hand this browser a fresh one
    response = JSONResponse({"ok": True})
    _set_session(response, user, request)
    return response


def _ws_allowed(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    # Reverse proxies often replace Host with the internal address but pass the
    # public one as X-Forwarded-Host. Accepting it is safe: a foreign page can't
    # set headers on a browser WebSocket handshake.
    hosts = {ws.headers.get("host")}
    forwarded = ws.headers.get("x-forwarded-host")
    if forwarded:
        hosts.add(forwarded.split(",")[0].strip())
    if origin and urlparse(origin).netloc not in hosts:
        return False  # cross-site WebSocket hijacking
    return get_settings().auth_disabled or auth().check_token(ws.cookies.get(COOKIE)) is not None


@app.get("/")
async def index():
    return _page("index.html")


@app.get("/api/status")
async def status():
    s, cfg = svc(), get_settings()
    return {
        "refreshing": s.refreshing,
        "last_refresh": s.last_refresh,
        "demo": cfg.demo,
        "version": __version__,
        "hidden": s.hidden,
        "host_script": {
            "version": s.wrapper_version,
            "required": REQUIRED_WRAPPER_VERSION,
            "outdated": s.wrapper_version is not None and s.wrapper_version < REQUIRED_WRAPPER_VERSION,
        },
        "queue": [i.as_dict() for i in s.queue],
        "maintenance": s.maintenance_status(),
        "purge": s.cleanup.as_dict() if s.cleanup else None,  # "Clear history" deleting snapshots/backups
        "cleanup": cfg.cleanup,  # default of the "clean up" checkbox
        "backup": {
            "mode": cfg.backup_mode,
            "keep": cfg.snapshot_keep if cfg.backup_mode == "snapshot" else cfg.backup_keep,
            "storage": cfg.backup_storage or None,
        },
    }


@app.get("/api/containers")
async def containers():
    s = svc()
    return [{**c, "busy": s.busy(c["vmid"]), "low_disk": s.low_disk(c), "self": c["vmid"] == s.self_vmid}
            for c in s.db.containers()]


def _not_self(s, vmid: int, what: str) -> None:
    """A rollback / restore stops the guest - for LUM's own container that would cut
    the job off halfway and could leave it stopped."""
    if vmid == s.self_vmid:
        raise HTTPException(409, f"LUM runs in this container and can't {what} it itself - "
                                 "do it in the Proxmox UI (the snapshots / backups are listed under Backups)")


@app.post("/api/sync")
async def sync():
    """Look for new or removed containers/VMs without checking packages."""
    try:
        return await svc().sync_guests()
    except Exception as err:
        raise HTTPException(502, f"cannot read the list from the host: {err}")


@app.post("/api/refresh", status_code=202)
async def refresh():
    asyncio.create_task(svc().refresh_all())
    return {"started": True}


@app.post("/api/containers/{vmid}/check")
async def check(vmid: int):
    s = svc()
    if not s.db.container(vmid):
        raise HTTPException(404, "unknown container")
    await s.check(vmid)
    return s.db.container(vmid)


@app.post("/api/containers/{vmid}/update", status_code=202)
async def update(vmid: int, kind: Literal["os", "app"] = "os", backup: bool = True, cleanup: bool | None = None):
    s = svc()
    c = s.db.container(vmid)
    if not c:
        raise HTTPException(404, "unknown container")
    if c["status"] != "running":
        raise HTTPException(409, "container is not running")
    if kind == "app" and c["type"] == "qemu":
        raise HTTPException(409, "app updates are only supported for LXC containers")
    if kind == "app" and c["self_created"]:
        raise HTTPException(409, "the container is tagged self-created - no app updates")
    if kind == "app" and not c["community_script"]:
        raise HTTPException(409, "container has no community-scripts update command")
    try:
        clean = get_settings().cleanup if cleanup is None else cleanup
        return s.start_job(vmid, kind, backup=backup, cleanup=clean).as_dict()
    except RuntimeError as err:
        raise HTTPException(409, str(err))


class QueueBody(BaseModel):
    vmids: list[int]
    kind: Literal["os", "app"] = "os"
    backup: bool = True
    cleanup: bool | None = None  # None: LUM_CLEANUP


class AutoBody(BaseModel):
    vmids: list[int]
    mode: Literal["off", "os", "all"]


@app.post("/api/auto")
async def set_auto(body: AutoBody):
    """Auto-update per guest: off, os (OS updates) or all (OS and app updates)."""
    s = svc()
    unknown = [v for v in body.vmids if not s.db.container(v)]
    if unknown:
        raise HTTPException(404, f"unknown container(s): {', '.join(map(str, unknown))}")
    s.db.set_auto_update(body.vmids, body.mode)
    return {"updated": len(body.vmids), "mode": body.mode}


@app.post("/api/notify/test")
async def notify_test():
    """Send a test message with the saved Telegram settings."""
    try:
        await svc().notifier.send("✅ <b>LUM</b>: test notification – Telegram works.", test=True)
    except RuntimeError as err:
        raise HTTPException(502, str(err))
    return {"sent": True}


@app.post("/api/maintenance/run", status_code=202)
async def maintenance_run():
    """Run the automatic updates now (without a window end)."""
    try:
        svc().start_maintenance()
    except RuntimeError as err:
        raise HTTPException(409, str(err))
    return {"started": True}


@app.get("/api/queue")
async def queue():
    return [i.as_dict() for i in svc().queue]


@app.post("/api/queue", status_code=202)
async def queue_add(body: QueueBody):
    """Update several guests one after the other; guests with nothing to do are skipped."""
    s = svc()
    unknown = [v for v in body.vmids if not s.db.container(v)]
    if unknown:
        raise HTTPException(404, f"unknown container(s): {', '.join(map(str, unknown))}")
    if not body.vmids:
        raise HTTPException(400, "no guests selected")
    clean = get_settings().cleanup if body.cleanup is None else body.cleanup
    added = s.enqueue(body.vmids, body.kind, body.backup, clean)
    return {"added": added, "queue": [i.as_dict() for i in s.queue]}


@app.delete("/api/queue")
async def queue_cancel():
    """Cancel the waiting guests (a running update finishes), or clear a finished queue."""
    s = svc()
    s.cancel_queue()
    return {"queue": [i.as_dict() for i in s.queue]}


@app.post("/api/containers/{vmid}/restart", status_code=202)
async def restart(vmid: int):
    """Reboot the guest (e.g. after updates that replaced libraries or the kernel) → job"""
    s = svc()
    c = s.db.container(vmid)
    if not c:
        raise HTTPException(404, "unknown container")
    if c["status"] != "running":
        raise HTTPException(409, "container is not running")
    try:
        return s.start_job(vmid, "restart").as_dict()
    except RuntimeError as err:
        raise HTTPException(409, str(err))


@app.get("/api/containers/{vmid}/snapshots")
async def snapshots(vmid: int):
    s = svc()
    if not s.db.container(vmid):
        raise HTTPException(404, "unknown container")
    try:
        return await s.host.snapshots(vmid)
    except Exception as err:
        raise HTTPException(502, f"cannot list snapshots: {err}")


@app.get("/api/containers/{vmid}/backups")
async def backups(vmid: int):
    s = svc()
    if not s.db.container(vmid):
        raise HTTPException(404, "unknown container")
    try:
        return await s.host.backups(vmid)
    except Exception as err:
        raise HTTPException(502, f"cannot list backups: {err}")


@app.delete("/api/containers/{vmid}/backups/{backup_id}")
async def delete_backup(vmid: int, backup_id: int):
    s = svc()
    if not s.db.container(vmid):
        raise HTTPException(404, "unknown container")
    try:
        await s.delete_backup(vmid, backup_id)
    except RuntimeError as err:
        raise HTTPException(409, str(err))
    except Exception as err:
        log.warning("deleting backup %s of %s failed: %s", backup_id, vmid, err)
        raise HTTPException(502, f"cannot delete backup: {err}")
    return {"deleted": backup_id}


@app.post("/api/containers/{vmid}/restore", status_code=202)
async def restore(vmid: int, backup: int):
    s = svc()
    if not s.db.container(vmid):
        raise HTTPException(404, "unknown container")
    _not_self(s, vmid, "restore")
    try:
        return s.start_job(vmid, "restore", str(backup)).as_dict()
    except RuntimeError as err:
        raise HTTPException(409, str(err))


@app.delete("/api/containers/{vmid}/snapshots/{name}")
async def delete_snapshot(vmid: int, name: str):
    s = svc()
    if not s.db.container(vmid):
        raise HTTPException(404, "unknown container")
    if not re.fullmatch(r"lum_[0-9_]{1,30}", name):
        raise HTTPException(400, "only snapshots created by the update manager (lum_*) can be deleted")
    try:
        await s.delete_snapshot(vmid, name)
    except RuntimeError as err:
        raise HTTPException(409, str(err))
    except Exception as err:
        log.warning("deleting snapshot %s of CT %s failed: %s", name, vmid, err)
        raise HTTPException(502, f"cannot delete snapshot: {err}")
    return {"deleted": name}


@app.post("/api/containers/{vmid}/rollback", status_code=202)
async def rollback(vmid: int, snapshot: str):
    s = svc()
    if not s.db.container(vmid):
        raise HTTPException(404, "unknown container")
    if not re.fullmatch(r"lum_[0-9_]{1,30}", snapshot):
        raise HTTPException(400, "only snapshots created by the update manager (lum_*) can be rolled back")
    _not_self(s, vmid, "roll back")
    try:
        return s.start_job(vmid, "rollback", snapshot).as_dict()
    except RuntimeError as err:
        raise HTTPException(409, str(err))


@app.get("/api/jobs/{job_id}")
async def job(job_id: int):
    j = svc().jobs.get(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    return {**j.as_dict(), "lines": j.lines}


@app.websocket("/ws/jobs/{job_id}")
async def job_stream(ws: WebSocket, job_id: int):
    if not _ws_allowed(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    j = svc().jobs.get(job_id)
    if not j:
        await ws.close(code=4404)
        return
    q = j.subscribe()
    try:
        while (line := await q.get()) is not None:
            await ws.send_text(line)
        await ws.send_json({"done": True, "success": j.success})
        await ws.close()
    except WebSocketDisconnect:
        pass
    finally:
        j.unsubscribe(q)


# --- settings (.env) ----------------------------------------------------------------

def _under_systemd() -> bool:
    return bool(os.environ.get("INVOCATION_ID"))  # set by systemd for its services


async def _restart_soon() -> None:
    await asyncio.sleep(1)  # let the response reach the browser first
    # a transient unit of its own: a child of this service would die with the restart
    await asyncio.create_subprocess_exec(
        "systemd-run", "--no-block", "--quiet", "systemctl", "restart", "lxc-update-manager"
    )


class SettingsBody(BaseModel):
    values: dict


@app.get("/api/settings")
async def settings_get():
    return {"fields": settings_edit.current(get_settings()), "restart_supported": _under_systemd()}


@app.post("/api/settings")
async def settings_save(body: SettingsBody):
    s = svc()
    try:
        values = settings_edit.validate(body.values)
    except ValueError as err:
        raise HTTPException(400, str(err))
    env = settings_edit.read_env()
    changes = {k: v for k, v in values.items() if env.get(k) != v}
    if not changes:
        return {"saved": [], "restart": False}
    if s._busy or s.refreshing or s.queue_active or s.cleanup_active or s.maintenance_running:
        raise HTTPException(409, "An update or check is running - save again when it has finished.")
    settings_edit.write(changes)
    log.info("settings changed in the web UI: %s", ", ".join(sorted(changes)))
    restart = _under_systemd()
    if restart:
        asyncio.create_task(_restart_soon())
    return {"saved": sorted(changes), "restart": restart}


# --- export / import -------------------------------------------------------------------

EXPORT_FORMAT = 1


@app.get("/api/export")
async def export(settings: bool = True, secrets: bool = False, guests: bool = True, history: bool = True):
    """Settings (secrets only on request), auto-update per guest and history as JSON."""
    s = svc()
    data: dict = {"lum_export": EXPORT_FORMAT, "version": __version__, "exported": time.time()}
    if settings:
        data["settings"] = settings_edit.export_values(secrets)
    if guests:
        data["guests"] = [{"vmid": c["vmid"], "name": c["name"], "auto_update": c["auto_update"] or "off"}
                          for c in s.db.containers()]
    if history:
        data["history"] = s.db.export_history()
    name = f"lum-export-{time.strftime('%Y%m%d-%H%M')}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{name}"'})


class ImportBody(BaseModel):
    data: dict
    settings: bool = True
    guests: bool = True
    history: bool = True


@app.post("/api/import")
async def import_(body: ImportBody):
    """Import an export: settings (validated, LUM restarts), auto-update of guests with
    the same ID and name, history entries not there yet."""
    s, d = svc(), body.data
    if d.get("lum_export") != EXPORT_FORMAT:
        raise HTTPException(400, "this is not a LUM export file")
    result: dict = {"settings": [], "guests": 0, "guests_skipped": [], "history": 0, "restart": False}
    changes: dict = {}
    if body.settings and d.get("settings"):
        values = {k: v for k, v in d["settings"].items() if k in settings_edit.EDITABLE}
        try:
            values = settings_edit.validate(values)
        except ValueError as err:
            raise HTTPException(400, f"settings: {err}")
        env = settings_edit.read_env()
        changes = {k: v for k, v in values.items() if env.get(k) != v}
        if changes and (s._busy or s.refreshing or s.queue_active or s.cleanup_active or s.maintenance_running):
            raise HTTPException(409, "An update or check is running - import again when it has finished.")
    if body.guests:
        here = {c["vmid"]: c for c in s.db.containers()}
        for g in d.get("guests") or []:
            c = here.get(int(g.get("vmid", 0)))
            mode = g.get("auto_update", "off")
            if not c or c["name"] != g.get("name") or mode not in ("off", "os", "all"):
                result["guests_skipped"].append(int(g.get("vmid", 0)))
                continue
            s.db.set_auto_update([c["vmid"]], mode)
            result["guests"] += 1
    if body.history:
        try:
            result["history"] = s.db.import_history(d.get("history") or [])
        except (KeyError, TypeError, ValueError) as err:
            raise HTTPException(400, f"history: unexpected entry ({err})")
    if changes:  # last: LUM restarts afterwards
        settings_edit.write(changes)
        log.info("settings imported: %s", ", ".join(sorted(changes)))
        result["settings"] = sorted(changes)
        result["restart"] = _under_systemd()
        if result["restart"]:
            asyncio.create_task(_restart_soon())
    return result


@app.get("/api/history")
async def history():
    return svc().db.history()


@app.delete("/api/history")
async def clear_history(snapshots: bool = False, backups: bool = False):
    """Remove the finished entries. snapshots / backups: also delete all of LUM's
    snapshots / vzdump backups of every managed guest (in the background)."""
    s = svc()
    if (snapshots or backups) and s.cleanup_active:
        raise HTTPException(409, "snapshots and backups are already being deleted")
    removed = s.db.clear_history()
    cleanup = s.start_cleanup(snapshots, backups).as_dict() if snapshots or backups else None
    return {"removed": removed, "purge": cleanup}


@app.delete("/api/history/{history_id}")
async def delete_history(history_id: int):
    s = svc()
    entry = s.db.history_entry(history_id)
    if not entry:
        raise HTTPException(404, "unknown entry")
    if entry["finished"] is None:
        raise HTTPException(409, "this job is still running")
    s.db.delete_history(history_id)
    return {"removed": history_id}


@app.delete("/api/history/{history_id}/backup")
async def delete_history_backup(history_id: int):
    s = svc()
    entry = s.db.history_entry(history_id)
    if not entry:
        raise HTTPException(404, "unknown entry")
    if entry["backup_kind"] != "vzdump" or entry["backup_removed"]:
        raise HTTPException(409, "this entry has no vzdump backup (any more)")
    if entry["finished"] is None:
        raise HTTPException(409, "this job is still running")
    try:
        return {"deleted": await s.delete_history_backup(entry)}
    except (RuntimeError, ValueError) as err:
        raise HTTPException(409, str(err))
    except Exception as err:
        log.warning("deleting the backup of history entry %s failed: %s", history_id, err)
        raise HTTPException(502, f"cannot delete backup: {err}")


@app.get("/api/history/{history_id}/log", response_class=PlainTextResponse)
async def history_log(history_id: int):
    text = svc().db.history_log(history_id)
    if text is None:
        raise HTTPException(404, "unknown entry")
    return text
