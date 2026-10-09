from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LUM_", env_file=".env", extra="ignore")

    pve_host: str = ""
    pve_port: int = 22
    pve_user: str = "root"
    ssh_key_path: Path = Path("data/id_ed25519")
    known_hosts_path: Path = Path("data/known_hosts")

    db_path: Path = Path("data/lum.db")
    check_interval_minutes: int = 360
    max_parallel_checks: int = 4
    # optional, raises the GitHub API limit from 60 to 5000 requests/hour
    github_token: str = ""

    # safety copy before every update
    backup_mode: Literal["snapshot", "vzdump", "none"] = "snapshot"
    snapshot_keep: int = 2
    backup_storage: str = ""  # vzdump target, e.g. "local" or a PBS storage
    backup_vzdump_mode: Literal["snapshot", "suspend", "stop"] = "snapshot"
    backup_keep: int = 2
    min_free_mb: int = 500  # free space needed in the guest's / before an update, 0 = no check
    cleanup: bool = True  # after an OS update: apt autoremove + apt clean (default of the checkbox)

    # maintenance window for automatic updates (see app/schedule.py); empty days = off
    auto_days: str = ""
    auto_time: str = "03:00"
    auto_until: str = ""
    auto_restart: bool = False  # afterwards restart guests that need it (not LUM's own)

    # notifications through a Telegram bot (app/notify.py)
    telegram_token: str = ""
    telegram_chat_id: str = ""
    notify: Literal["off", "failures", "all"] = "failures"  # per update / bulk update
    notify_updates: bool = True  # after the scheduled check: which updates are available

    # web UI login, set with: venv/bin/python -m app.passwd
    auth_file: Path = Path("data/auth.json")
    secret_file: Path = Path("data/secret.key")
    session_hours: int = 12
    # only when another layer already authenticates (e.g. a reverse proxy with SSO)
    auth_disabled: bool = False
    # set to true when the UI is served via HTTPS (reverse proxy)
    cookie_secure: bool = False

    demo: bool = False

    @model_validator(mode="after")
    def _check(self):
        if self.backup_mode == "vzdump" and not self.backup_storage:
            raise ValueError("LUM_BACKUP_MODE=vzdump needs LUM_BACKUP_STORAGE")
        if self.snapshot_keep < 1 or self.backup_keep < 1:
            raise ValueError("LUM_SNAPSHOT_KEEP / LUM_BACKUP_KEEP must be at least 1")
        from .schedule import parse_days, parse_time

        try:
            parse_days(self.auto_days)
            parse_time(self.auto_time)
            if self.auto_until:
                parse_time(self.auto_until)
        except ValueError as err:
            raise ValueError(f"auto-update window: {err}") from None
        import re

        if self.telegram_token and not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", self.telegram_token):
            raise ValueError("LUM_TELEGRAM_TOKEN looks wrong - it is like 123456789:AAH… (from @BotFather)")
        if self.telegram_chat_id and not re.fullmatch(r"-?\d+|@[A-Za-z][A-Za-z0-9_]{4,}", self.telegram_chat_id):
            raise ValueError("LUM_TELEGRAM_CHAT_ID: a number (groups are negative) or @channelname")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
