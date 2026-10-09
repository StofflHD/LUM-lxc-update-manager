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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
