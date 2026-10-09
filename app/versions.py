"""Compare version strings from very different projects.

Good enough for what the community scripts install: dotted numbers with an optional
leading "v" / prefix and an optional suffix. Pre-releases (a, alpha, b, beta, rc,
pre, preview, dev) rank below the release with the same numbers, so
0.44.0 < 0.45.0a1 < 0.45.0rc2 < 0.45.0. Trailing zeros don't count (1.0 == 1.0.0).
"""

import re

_SPLIT = re.compile(r"^(?P<rel>\d+(?:[._]\d+)*)(?P<rest>.*)$")
_PRE = re.compile(r"^(?P<tag>dev|a|alpha|b|beta|c|rc|pre|preview)\.?(?P<num>\d*)")
_PRE_ORDER = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3, "pre": 3, "preview": 3}


def _clean(version: str) -> str:
    version = (version or "").strip()
    m = re.search(r"\d", version)
    return version[m.start():] if m else version  # "v1.2", "version/1.2" -> "1.2"


def version_key(version: str) -> tuple | None:
    """Sortable key, or None if the string doesn't look like a version."""
    m = _SPLIT.match(_clean(version))
    if not m:
        return None
    release = [int(x) for x in re.split(r"[._]", m.group("rel"))]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    rest = m.group("rest").lower().lstrip(".-_~")
    pre = _PRE.match(rest)
    if pre:
        stage = (0, _PRE_ORDER[pre.group("tag")], int(pre.group("num") or 0))
    else:
        stage = (1, 0, 0)  # final release (also "+build", "-1", "post1" …)
    return (tuple(release), stage)


def compare(a: str, b: str) -> int | None:
    """-1 / 0 / 1 like a < b, a == b, a > b; None if one of them isn't comparable."""
    ka, kb = version_key(a), version_key(b)
    if ka is None or kb is None:
        return None
    return (ka > kb) - (ka < kb)


def is_update(installed: str | None, latest: str | None) -> bool:
    """True if latest is newer than installed. Unreadable versions: any difference counts."""
    if not installed or not latest:
        return False
    result = compare(latest, installed)
    if result is None:
        return installed != latest
    return result > 0


def is_ahead(installed: str | None, latest: str | None) -> bool:
    """True if the installed version is newer than the latest release (e.g. a pre-release)."""
    if not installed or not latest:
        return False
    return compare(installed, latest) == 1
