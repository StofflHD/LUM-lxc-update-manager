"""Maintenance window for automatic updates: days, start time, optional end.

LUM_AUTO_DAYS: "daily", or days and ranges like "sun", "sat,sun", "mon-fri" (empty = off)
LUM_AUTO_TIME: start "HH:MM" (local time of the LUM container)
LUM_AUTO_UNTIL: optional end "HH:MM" - guests not started by then wait for the next window;
                an end before the start means the next day (e.g. 23:00 - 02:00)
"""

import re
from datetime import datetime, time, timedelta

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
START_SLOT = timedelta(minutes=5)  # the window starts if LUM looks within these minutes


def parse_days(text: str) -> set[int]:
    """Weekdays (0 = Monday); empty set = no window."""
    text = text.strip().lower()
    if not text:
        return set()
    if text == "daily":
        return set(range(7))
    days: set[int] = set()
    for part in filter(None, (p.strip() for p in text.split(","))):
        first, _, last = part.partition("-")
        if first not in DAYS or (last and last not in DAYS):
            raise ValueError(f"unknown day '{part}' - use mon,tue,…,sun, ranges like mon-fri, or daily")
        a, b = DAYS.index(first), DAYS.index(last or first)
        days.update(range(a, b + 1) if a <= b else [*range(a, 7), *range(0, b + 1)])
    return days


def parse_time(text: str) -> time:
    m = _TIME.match(text.strip())
    if not m:
        raise ValueError(f"'{text}' is not a time like 03:00")
    return time(int(m.group(1)), int(m.group(2)))


def window_start(days: set[int], start: time, now: datetime) -> datetime | None:
    """Start of the window that is open right now (within START_SLOT of its start)."""
    begin = datetime.combine(now.date(), start)
    if now.weekday() in days and begin <= now < begin + START_SLOT:
        return begin
    return None


def next_start(days: set[int], start: time, now: datetime) -> datetime | None:
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        begin = datetime.combine(day, start)
        if day.weekday() in days and begin > now:
            return begin
    return None


def window_end(begin: datetime, until: time | None) -> datetime | None:
    if until is None:
        return None
    end = datetime.combine(begin.date(), until)
    return end if end > begin else end + timedelta(days=1)


def describe(days: set[int]) -> str:
    if len(days) == 7:
        return "daily"
    return ",".join(DAYS[d] for d in sorted(days))
