"""Latest-version lookup for community-scripts apps.

Mirrors what community-scripts' own update-apps.sh does for its dry run:
the ct/<script>.sh contains `check_for_gh_release "<app>" "<owner/repo>"`;
<app> names the version file (~/.<app>) inside the container and
<owner/repo> is where the latest release comes from.
"""

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

SCRIPT_URL = "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/{}.sh"
RELEASE_URL = "https://api.github.com/repos/{}/releases/latest"
_CHECK_RE = re.compile(r'check_for_gh_release\s+"([^"]+)"\s+"([^"/\s]+/[^"\s]+)"')

SCRIPT_TTL = 24 * 3600
RELEASE_TTL = 3600


@dataclass
class AppSource:
    app: str  # version file name, lowercase, no spaces
    repo: str  # GitHub owner/repo


def strip_v(version: str) -> str:
    version = version.strip()
    return version[1:] if re.match(r"^[vV]\d", version) else version


class AppCatalog:
    def __init__(self, github_token: str = ""):
        self._token = github_token
        self._sources: dict[str, tuple[float, AppSource | None]] = {}
        self._releases: dict[str, tuple[float, str | None]] = {}

    def _get(self, url: str, accept: str) -> str:
        headers = {"Accept": accept, "User-Agent": "lxc-update-manager"}
        if self._token and "api.github.com" in url:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.read().decode()

    async def source(self, script: str) -> AppSource | None:
        """Parse the ct script once a day; None if it doesn't use GitHub releases."""
        cached = self._sources.get(script)
        if cached and time.time() - cached[0] < SCRIPT_TTL:
            return cached[1]
        try:
            text = await asyncio.to_thread(self._get, SCRIPT_URL.format(script), "text/plain")
        except (urllib.error.URLError, TimeoutError) as err:
            log.warning("cannot fetch ct/%s.sh: %s", script, err)
            return cached[1] if cached else None
        match = _CHECK_RE.search(text)
        src = AppSource(app=match[1].lower().replace(" ", ""), repo=match[2]) if match else None
        self._sources[script] = (time.time(), src)
        return src

    async def latest(self, repo: str) -> str | None:
        cached = self._releases.get(repo)
        if cached and time.time() - cached[0] < RELEASE_TTL:
            return cached[1]
        try:
            data = json.loads(
                await asyncio.to_thread(self._get, RELEASE_URL.format(repo), "application/vnd.github+json")
            )
            tag = strip_v(data.get("tag_name", "")) or None
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            # 403 here usually means the unauthenticated rate limit (60/h) -> LUM_GITHUB_TOKEN
            log.warning("cannot fetch latest release of %s: %s", repo, err)
            return cached[1] if cached else None
        self._releases[repo] = (time.time(), tag)
        return tag
