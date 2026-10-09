"""Read and update .env from the web UI.

Only a safe subset is editable. Connection settings are shown read-only (a wrong
host or key would lock LUM out, and the host key is pinned to the IP), paths,
LUM_AUTH_DISABLED and LUM_DEMO are not exposed at all.

Existing lines, comments and unknown keys in .env are kept; a key is updated in
place (also if it only exists commented out, like "#FORWARDED_ALLOW_IPS=") or
appended.
"""

import ipaddress
import re
from pathlib import Path

from pydantic import ValidationError

from .config import Settings

ENV_FILE = Path(".env")
_LINE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=(.*)$")
# values end up in a systemd EnvironmentFile: keep them free of quotes, spaces etc.
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9_.,:/@+-]*$")

# key, group, label, kind, extra
FIELDS = [
    ("LUM_PVE_HOST", "Connection", "Proxmox host", "readonly", {}),
    ("LUM_PVE_PORT", "Connection", "SSH port", "readonly", {}),
    ("LUM_PVE_USER", "Connection", "SSH user", "readonly", {}),
    ("LUM_CHECK_INTERVAL_MINUTES", "Checks", "Check all every … minutes", "int", {"min": 5, "max": 10080}),
    ("LUM_MAX_PARALLEL_CHECKS", "Checks", "Guests checked at the same time", "int", {"min": 1, "max": 16}),
    ("LUM_GITHUB_TOKEN", "Checks", "GitHub token (optional, for app versions)", "secret", {}),
    ("LUM_MIN_FREE_MB", "Updates", "Free space needed in the guest (MB, 0 = off)", "int", {"min": 0, "max": 100000}),
    ("LUM_CLEANUP", "Updates", "Clean up after OS updates (apt autoremove, apt clean)", "bool", {}),
    ("LUM_BACKUP_MODE", "Backup", "Backup before an update", "choice", {"options": ["snapshot", "vzdump", "none"]}),
    ("LUM_SNAPSHOT_KEEP", "Backup", "Snapshots kept per guest", "int", {"min": 1, "max": 50}),
    ("LUM_BACKUP_STORAGE", "Backup", "vzdump storage", "text", {"placeholder": "e.g. local or pbs"}),
    ("LUM_BACKUP_VZDUMP_MODE", "Backup", "vzdump mode", "choice", {"options": ["snapshot", "suspend", "stop"]}),
    ("LUM_BACKUP_KEEP", "Backup", "vzdump backups kept per guest", "int", {"min": 1, "max": 50}),
    ("LUM_AUTO_DAYS", "Auto-update", "Days (empty = off)", "text", {"placeholder": "e.g. sun, sat,sun, mon-fri, daily"}),
    ("LUM_AUTO_TIME", "Auto-update", "Start (HH:MM)", "text", {"placeholder": "03:00"}),
    ("LUM_AUTO_UNTIL", "Auto-update", "End (HH:MM, optional)", "text", {"placeholder": "e.g. 05:00"}),
    ("LUM_AUTO_RESTART", "Auto-update", "Restart guests that need it afterwards", "bool", {}),
    ("LUM_SESSION_HOURS", "Login & reverse proxy", "Session lifetime (hours)", "int", {"min": 1, "max": 720}),
    ("LUM_COOKIE_SECURE", "Login & reverse proxy", "Force Secure cookie", "bool", {}),
    ("FORWARDED_ALLOW_IPS", "Login & reverse proxy", "Reverse proxy IP(s)", "text",
     {"placeholder": "e.g. 192.168.1.20 (comma separated)"}),
]
EDITABLE = {k for k, _, _, kind, _ in FIELDS if kind != "readonly"}


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Active (uncommented) KEY=value pairs."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            m = _LINE.match(line)
            if m:
                values[m.group(1)] = m.group(2).strip()
    return values


def current(settings: Settings) -> list[dict]:
    """Fields with their effective values for the UI (secrets only as 'set or not')."""
    env = read_env()
    out = []
    for key, group, label, kind, extra in FIELDS:
        if key == "FORWARDED_ALLOW_IPS":
            value = env.get(key, "")
        else:
            value = getattr(settings, key.removeprefix("LUM_").lower())
        if kind == "secret":
            value = bool(value)  # never send the token itself
        elif kind != "bool":
            value = "" if value is None else str(value)
        out.append({"key": key, "group": group, "label": label, "kind": kind, "value": value, **extra})
    return out


def _check_ips(value: str) -> None:
    for part in filter(None, (p.strip() for p in value.split(","))):
        try:
            ipaddress.ip_network(part, strict=False)
        except ValueError:
            raise ValueError(f"FORWARDED_ALLOW_IPS: '{part}' is not an IP address or network") from None


def validate(changes: dict) -> dict[str, str]:
    """Normalise the submitted values to .env strings; raises ValueError with a message."""
    unknown = set(changes) - EDITABLE
    if unknown:
        raise ValueError(f"not editable: {', '.join(sorted(unknown))}")
    kinds = {k: (kind, extra) for k, _, _, kind, extra in FIELDS}
    out: dict[str, str] = {}
    for key, raw in changes.items():
        kind, extra = kinds[key]
        if kind == "secret" and (raw is None or raw == ""):
            continue  # empty = keep the stored token
        if kind == "bool":
            out[key] = "true" if raw in (True, "true", "1", "on") else "false"
        elif kind == "int":
            try:
                n = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{key}: a whole number is needed") from None
            if not extra["min"] <= n <= extra["max"]:
                raise ValueError(f"{key}: between {extra['min']} and {extra['max']}")
            out[key] = str(n)
        else:
            value = str(raw).strip()
            if kind == "choice" and value not in extra["options"]:
                raise ValueError(f"{key}: one of {', '.join(extra['options'])}")
            if not _SAFE_TEXT.match(value):
                raise ValueError(f"{key}: only letters, digits and . , : / @ + - _ are allowed")
            out[key] = value
    if "FORWARDED_ALLOW_IPS" in out:
        _check_ips(out["FORWARDED_ALLOW_IPS"])
    if out.get("LUM_GITHUB_TOKEN") == "-":  # explicit removal
        out["LUM_GITHUB_TOKEN"] = ""

    # the whole configuration must still be valid (e.g. vzdump needs a storage)
    merged = {**read_env(), **out}
    lum = {k.removeprefix("LUM_").lower(): v for k, v in merged.items() if k.startswith("LUM_")}
    try:
        Settings(_env_file=None, **lum)
    except ValidationError as err:
        raise ValueError("; ".join(e["msg"].removeprefix("Value error, ") for e in err.errors())) from None
    return out


def write(changes: dict[str, str], path: Path = ENV_FILE) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(changes)
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if m and m.group(1) in pending:
            key = m.group(1)
            commented = line.lstrip().startswith("#")
            # replace the active line; a commented template line only if there is no active one
            if not commented or key not in read_env(path):
                lines[i] = f"{key}={pending.pop(key)}"
    for key, value in pending.items():
        lines.append(f"{key}={value}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    tmp.chmod(0o600)
    tmp.replace(path)
