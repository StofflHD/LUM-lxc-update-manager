"""Installed vs. latest version of community-script apps.

The ct/<script>.sh of an app tells where its version comes from:

- check_for_gh_release / check_for_codeberg_release / check_for_gl_release
  "<app>" "<owner/repo>" [pinned] [reason] [tag prefix]: a forge release. Like the
  community scripts (tools forge.func) the latest version is the *highest* stable
  release (no drafts / pre-releases), optionally only tags with the prefix. A pinned
  version holds the app back on purpose.
- check_for_gh_tag "<app>" "<owner/repo>" [prefix]: the newest Git tag.
  For all of these the installed version is in ~/.<app> inside the container.
- no check, but `pip install … --upgrade` / `npm … -g` in update_script(): PyPI / npm,
  installed version via pip / npm in the container.
- no check, update_script() only upgrades OS packages: the app comes with the OS
  updates; no Docker / unknown: no version check possible.
- update_script() does nothing but print a message ("The app offers a built-in
  updater", "updates itself automatically", "no update function"): the app is updated
  in the app itself - an app update would change nothing.
"""

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .versions import version_key

log = logging.getLogger(__name__)

SCRIPT_URL = "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/{}.sh"
SCRIPT_TTL = 24 * 3600
LATEST_TTL = 3600

FORGES = {"gh_release": "github", "codeberg_release": "codeberg", "gl_release": "gitlab", "gh_tag": "gh_tag"}
_CHECK = re.compile(
    r'(?:GITLAB_URL="(?P<gitlab>[^"]+)"\s+)?check_for_(?P<kind>gh_release|codeberg_release|gl_release|gh_tag)'
    r'(?P<args>(?:[ \t]+"[^"\n]*")+)'
)
_ARG = re.compile(r'"([^"\n]*)"')
_DEPLOY = re.compile(
    r'fetch_and_deploy_(?P<kind>gh|codeberg|gl)_release[ \t]+"(?P<app>[^"\n]+)"[ \t]+"(?P<repo>[^"\n]+/[^"\n]+)"'
)
_PIP = re.compile(r'\bpip3?\s+install\b([^\n;&|]*)')
_NPM = re.compile(r'\bnpm\s+(?:install|i|update|upgrade)\b([^\n;&|]*)')
_PKG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$|^@[a-z0-9-]+/[a-z0-9._-]+$")


@dataclass
class AppSource:
    kind: str  # github | codeberg | gitlab | gh_tag | pypi | npm | os | docker | builtin | none
    app: str = ""  # version file name (~/.<app>) for forge kinds
    repo: str = ""  # owner/repo, or the package name for pypi / npm
    host: str = ""  # gitlab instance
    pinned: str = ""  # version the script holds the app at
    pin_reason: str = ""
    prefix: str = ""  # only tags starting with this
    hint: str = ""  # builtin: the script's message, e.g. "The app offers a built-in updater"

    @property
    def has_versions(self) -> bool:
        return self.kind in ("github", "codeberg", "gitlab", "gh_tag", "pypi", "npm")

    @property
    def url(self) -> str:
        return {
            "github": f"https://github.com/{self.repo}/releases",
            "gh_tag": f"https://github.com/{self.repo}/tags",
            "codeberg": f"https://codeberg.org/{self.repo}/releases",
            "gitlab": f"{self.host or 'https://gitlab.com'}/{self.repo}/-/releases",
            "pypi": f"https://pypi.org/project/{self.repo}/",
            "npm": f"https://www.npmjs.com/package/{self.repo}",
        }.get(self.kind, "")

    @property
    def label(self) -> str:
        return {"pypi": f"PyPI: {self.repo}", "npm": f"npm: {self.repo}"}.get(self.kind, self.repo)


def strip_v(version: str) -> str:
    version = version.strip()
    return version[1:] if re.match(r"^[vV]\d", version) else version


def normalize(version: str) -> str:
    """For comparing: drop a leading 'v' or a non-numeric prefix such as 'version/'."""
    version = strip_v(version or "")
    m = re.search(r"\d", version)
    return version[m.start():] if m else version


def _version_key(tag: str) -> tuple:
    # pre-releases below the final release, like versions.compare
    return version_key(tag) or ((0,), (0, 0, 0))


def _update_body(text: str) -> str:
    m = re.search(r"function update_script\(\)\s*\{(.*?)\n\}", text, re.S)
    return m.group(1) if m else ""


# anything that changes the container; an update_script() without any of it only prints
# a message (after the usual "No installation found" guard)
_DOES_WORK = re.compile(
    r"fetch_and_deploy|\bapt(-get)?\b|\bapk\b|\bpip3?\b|\buv\b|\bnpm\b|\bpnpm\b|\byarn\b|docker|\bcurl\b"
    r"|\bwget\b|\bgit\b|run_os_update|\$STD|systemctl|\bcp\b|\bmv\b|\btar\b|\bunzip\b|bash -c|\bsed\b"
    r"|\bcomposer\b|\bphp\b|\bcargo\b|\bgo\b|\bdotnet\b|\bmake\b|setup_|check_for_|\bupdate\b\s*$",
    re.M,
)
_GUARD = re.compile(r"if \[\[[^\n]*\]\]; then\s*msg_error[^\n]*\n\s*exit\s*\n\s*fi")
_MSG = re.compile(r'msg_\w+\s+(?:"[^"]*"\s+"[^"]*"\s+)?"([^"]+)"')


def _builtin_hint(body: str, app: str) -> str | None:
    """The script's message if update_script() does no work at all, else None."""
    rest = _GUARD.sub("", re.sub(r"header_info|check_container_storage|check_container_resources", "", body))
    if not body.strip() or _DOES_WORK.search(rest):
        return None
    msgs = [m.replace("${APP}", app) for m in _MSG.findall(rest)]
    return msgs[-1] if msgs else "the community script has no update for this app"


def _first_package(args: str) -> str | None:
    for token in args.split():
        token = token.split("==")[0].split("@latest")[0]
        if token.startswith("-") or "$" in token or "/" in token and not token.startswith("@"):
            continue
        if _PKG_NAME.match(token):
            return token
    return None


def parse_script(text: str, script: str) -> AppSource:
    """Work out where the app's version comes from (see module docstring)."""
    calls = []
    for m in _CHECK.finditer(text):
        args = _ARG.findall(m.group("args"))
        if len(args) >= 2 and "/" in args[1]:
            calls.append((m.group("kind"), m.group("gitlab") or "", args))
    if calls:
        want = script.lower().replace("-", "")

        def score(call):
            app = call[2][0].lower().replace(" ", "").replace("-", "").replace("_", "")
            return 3 if app == want else 2 if want in app or app in want else 0

        kind, gitlab, args = max(calls, key=score)  # first one wins a tie
        if kind == "gh_tag":
            return AppSource("gh_tag", app=args[0].lower().replace(" ", ""), repo=args[1],
                             prefix=args[2] if len(args) > 2 else "")
        pinned = args[2] if len(args) > 2 else ""
        var = re.fullmatch(r"\$\{?(\w+)\}?", pinned)
        if var:  # pinned via a variable set in the script
            found = re.search(rf'\b{var.group(1)}="([^"$]+)"', text)
            pinned = found.group(1) if found else ""
        return AppSource(FORGES[kind], app=args[0].lower().replace(" ", ""), repo=args[1], host=gitlab,
                         pinned=pinned, pin_reason=args[3] if len(args) > 3 and pinned else "",
                         prefix=args[4] if len(args) > 4 else "")

    body = _update_body(text)
    app_name = re.search(r'^APP="([^"]+)"', text, re.M)
    hint = _builtin_hint(body, app_name.group(1) if app_name else script)
    if hint:
        return AppSource("builtin", hint=hint)
    # no check, but the update deploys the app's release - that also writes ~/.<app>.
    # Only when the name matches the script: scripts also deploy drivers and helpers.
    want = re.sub(r"[^a-z0-9]", "", script.lower())
    for m in _DEPLOY.finditer(body):
        app = re.sub(r"[^a-z0-9]", "", m.group("app").lower())
        if app and (app in want or want in app):
            kind = {"gh": "github", "codeberg": "codeberg", "gl": "gitlab"}[m.group("kind")]
            return AppSource(kind, app=m.group("app").lower().replace(" ", ""), repo=m.group("repo"))
    # Docker first: such scripts may pip-install helper tools (e.g. runlike)
    if re.search(r"\bdocker\b", body):
        return AppSource("docker")
    for regex, kind in ((_PIP, "pypi"), (_NPM, "npm")):
        for m in regex.finditer(body):
            if kind == "npm" and not re.search(r"(?:^|\s)(?:-g|--global)\b", m.group(1)):
                continue
            pkg = _first_package(m.group(1))
            if pkg:
                return AppSource(kind, repo=pkg)
    if re.search(r"run_os_update|apt(?:-get)?\s+(?:-y\s+)?(?:upgrade|dist-upgrade)|apk\s+(?:-U\s+)?upgrade", body) \
            and "fetch_and_deploy" not in body:
        return AppSource("os")
    return AppSource("none")


class AppCatalog:
    def __init__(self, github_token: str = ""):
        self._token = github_token
        self._sources: dict[str, tuple[float, AppSource | None]] = {}
        self._latest: dict[tuple, tuple[float, str | None]] = {}

    def _get(self, url: str, accept: str = "application/json") -> str:
        headers = {"Accept": accept, "User-Agent": "lxc-update-manager"}
        if self._token and url.startswith("https://api.github.com/"):
            headers["Authorization"] = f"Bearer {self._token}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as res:
            return res.read().decode()

    async def _json(self, url: str):
        return json.loads(await asyncio.to_thread(self._get, url))

    async def source(self, script: str) -> AppSource | None:
        """Parse ct/<script>.sh once a day; None if it can't be fetched."""
        cached = self._sources.get(script)
        if cached and time.time() - cached[0] < SCRIPT_TTL:
            return cached[1]
        try:
            text = await asyncio.to_thread(self._get, SCRIPT_URL.format(script), "text/plain")
        except (urllib.error.URLError, TimeoutError) as err:
            log.warning("cannot fetch ct/%s.sh: %s", script, err)
            return cached[1] if cached else None
        src = parse_script(text, script)
        self._sources[script] = (time.time(), src)
        return src

    async def latest(self, src: AppSource) -> str | None:
        if src.pinned:  # the script holds the app at this version
            return normalize(src.pinned)
        if not src.has_versions or src.kind in ("os", "docker", "builtin", "none"):
            return None
        key = (src.kind, src.host, src.repo, src.prefix)
        cached = self._latest.get(key)
        if cached and time.time() - cached[0] < LATEST_TTL:
            return cached[1]
        try:
            version = await self._lookup(src)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, TypeError) as err:
            # 403/429 is usually a rate limit (GitHub: 60/h without LUM_GITHUB_TOKEN)
            log.warning("cannot get the latest version of %s (%s): %s", src.repo, src.kind, err)
            return cached[1] if cached else None
        self._latest[key] = (time.time(), version)
        return version

    async def _lookup(self, src: AppSource) -> str | None:
        repo = src.repo
        if src.kind == "pypi":
            return (await self._json(f"https://pypi.org/pypi/{urllib.parse.quote(repo)}/json"))["info"]["version"]
        if src.kind == "npm":
            return (await self._json(f"https://registry.npmjs.org/{repo.replace('/', '%2F')}/latest"))["version"]
        if src.kind == "gh_tag":
            if src.prefix:
                refs = await self._json(f"https://api.github.com/repos/{repo}/git/matching-refs/tags/{src.prefix}")
                tags = [r["ref"].removeprefix("refs/tags/") for r in refs]
                return normalize(max(tags, key=_version_key)) if tags else None
            tags = await self._json(f"https://api.github.com/repos/{repo}/tags?per_page=1")
            return normalize(tags[0]["name"]) if tags else None

        # forge releases: the highest stable one, like the community scripts pick it
        if src.kind == "github":
            releases = await self._json(f"https://api.github.com/repos/{repo}/releases?per_page=100")
        elif src.kind == "codeberg":
            releases = await self._json(f"https://codeberg.org/api/v1/repos/{repo}/releases?limit=100")
        else:  # gitlab
            base = src.host or "https://gitlab.com"
            releases = await self._json(f"{base}/api/v4/projects/{urllib.parse.quote(repo, safe='')}"
                                        "/releases?per_page=100&order_by=released_at&sort=desc")
        tags = [
            r["tag_name"] for r in releases
            if not r.get("draft") and not r.get("prerelease") and not r.get("upcoming_release")
            and r.get("tag_name", "").startswith(src.prefix)
        ]
        return normalize(max(tags, key=_version_key)) if tags else None
