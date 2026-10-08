from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _read_version() -> str:
    try:
        return (_ROOT / "VERSION").read_text().strip()
    except OSError:
        return "dev"


def _required_wrapper_version() -> int:
    """WRAPPER_VERSION of the host script shipped with this version."""
    try:
        for line in (_ROOT / "host" / "lxc-update-wrapper.sh").read_text().splitlines():
            if line.startswith("WRAPPER_VERSION="):
                return int(line.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return 0


__version__ = _read_version()
REQUIRED_WRAPPER_VERSION = _required_wrapper_version()
