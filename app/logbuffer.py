"""The last log lines in memory, for the "Server log" in the web UI.

Everything LUM logs since its start (and uvicorn's errors). The access log - one line
per request, mostly the page polling every few seconds - is left out. Older lines:
journalctl -u lxc-update-manager in the container.
"""

import collections
import logging
import time

MAX_LINES = 2000


class RingHandler(logging.Handler):
    def __init__(self, size: int = MAX_LINES):
        super().__init__(logging.INFO)
        self.lines: collections.deque = collections.deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info:
                text += "\n" + logging.Formatter().formatException(record.exc_info)
            self.lines.append({"time": record.created, "level": record.levelname, "name": record.name, "text": text})
        except Exception:  # logging must never break the app
            self.handleError(record)


class _NoAccess(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name != "uvicorn.access"


handler = RingHandler()
handler.addFilter(_NoAccess())
started = time.time()


def install() -> None:
    """Attach to the root logger and uvicorn's error logger (which doesn't propagate)."""
    for name in ("", "uvicorn.error"):
        logger = logging.getLogger(name)
        if handler not in logger.handlers:
            logger.addHandler(handler)


def lines(min_level: str = "INFO") -> list[dict]:
    floor = logging.getLevelName(min_level)
    floor = floor if isinstance(floor, int) else logging.INFO
    return [entry for entry in handler.lines if logging.getLevelName(entry["level"]) >= floor]
