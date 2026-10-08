import asyncio
import logging
import math
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import REQUIRED_WRAPPER_VERSION, __version__
from .apps import AppCatalog
from .auth import COOKIE, Auth, write_credentials
from .config import get_settings
from .db import Database
from .host import HostClient
from .service import UpdateService, scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
# reachable without a session
PUBLIC = {"/login", "/api/login", "/api/auth/state", "/static/style.css", "/static/login.js"}


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
    task = asyncio.create_task(scheduler(app.state.service, settings.check_interval_minutes))
    yield
    task.cancel()


app = FastAPI(title="LXC Update Manager", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


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


def _set_session(response: JSONResponse, username: str) -> None:
    response.set_cookie(
        COOKIE,
        auth().make_token(username),
        max_age=auth().session_seconds,
        httponly=True,
        samesite="strict",
        secure=get_settings().cookie_secure,
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


@app.get("/login")
async def login_page(request: Request):
    if get_settings().auth_disabled or auth().check_token(request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "login.html")


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
    _set_session(response, body.username)
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
    _set_session(response, user)
    return response


def _ws_allowed(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if origin and urlparse(origin).netloc != ws.headers.get("host"):
        return False  # cross-site WebSocket hijacking
    return get_settings().auth_disabled or auth().check_token(ws.cookies.get(COOKIE)) is not None


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
async def status():
    s, cfg = svc(), get_settings()
    return {
        "refreshing": s.refreshing,
        "last_refresh": s.last_refresh,
        "demo": cfg.demo,
        "version": __version__,
        "host_script": {
            "version": s.wrapper_version,
            "required": REQUIRED_WRAPPER_VERSION,
            "outdated": s.wrapper_version is not None and s.wrapper_version < REQUIRED_WRAPPER_VERSION,
        },
        "backup": {
            "mode": cfg.backup_mode,
            "keep": cfg.snapshot_keep if cfg.backup_mode == "snapshot" else cfg.backup_keep,
            "storage": cfg.backup_storage or None,
        },
    }


@app.get("/api/containers")
async def containers():
    s = svc()
    return [{**c, "busy": s.busy(c["vmid"])} for c in s.db.containers()]


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
async def update(vmid: int, kind: Literal["os", "app"] = "os", backup: bool = True):
    s = svc()
    c = s.db.container(vmid)
    if not c:
        raise HTTPException(404, "unknown container")
    if c["status"] != "running":
        raise HTTPException(409, "container is not running")
    if kind == "app" and not c["community_script"]:
        raise HTTPException(409, "container has no community-scripts update command")
    try:
        return s.start_job(vmid, kind, backup=backup).as_dict()
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
        raise HTTPException(502, f"cannot delete snapshot: {err}")
    return {"deleted": name}


@app.post("/api/containers/{vmid}/rollback", status_code=202)
async def rollback(vmid: int, snapshot: str):
    s = svc()
    if not s.db.container(vmid):
        raise HTTPException(404, "unknown container")
    if not re.fullmatch(r"lum_[0-9_]{1,30}", snapshot):
        raise HTTPException(400, "only snapshots created by the update manager (lum_*) can be rolled back")
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


@app.get("/api/history")
async def history():
    return svc().db.history()


@app.get("/api/history/{history_id}/log", response_class=PlainTextResponse)
async def history_log(history_id: int):
    text = svc().db.history_log(history_id)
    if text is None:
        raise HTTPException(404, "unknown entry")
    return text
