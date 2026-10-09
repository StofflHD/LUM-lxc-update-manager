import json
import re
import sqlite3
import time
from pathlib import Path

from .versions import is_ahead, is_update

SCHEMA = """
CREATE TABLE IF NOT EXISTS containers (
    vmid INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    pkg_manager TEXT,
    community_script INTEGER NOT NULL DEFAULT 0,
    upgradable TEXT NOT NULL DEFAULT '[]',
    last_check REAL,
    last_error TEXT,
    app_script TEXT,
    app_repo TEXT,
    app_installed TEXT,
    app_latest TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vmid INTEGER NOT NULL,
    kind TEXT NOT NULL,
    started REAL NOT NULL,
    finished REAL,
    success INTEGER,
    log TEXT NOT NULL DEFAULT '',
    backup_kind TEXT,
    backup_ref TEXT,
    backup_removed INTEGER NOT NULL DEFAULT 0,
    detail TEXT,
    name TEXT
);
"""

# history rows with the guest's name: the current one, or the one stored when the
# job ran if the guest no longer exists
HISTORY_COLS = ("h.id, h.vmid, h.kind, h.started, h.finished, h.success, h.backup_kind, h.backup_ref, "
                "h.backup_removed, h.detail, COALESCE(c.name, h.name) AS name")


# "apt list --upgradable" line: "openssl/stable-security 3.5.7-1~deb13u3 amd64 [...]";
# Ubuntu lists several suites: "libssl3t64/noble-updates,noble-security ...".
# apk has no security channel, so Alpine guests never show security updates.
_APT_SUITES = re.compile(r"^[^/\s]+/(\S+)\s")


def is_security(line: str) -> bool:
    m = _APT_SUITES.match(line)
    return bool(m) and any(s.endswith("-security") for s in m.group(1).split(","))


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after the first release to existing databases."""
        added = {
            "containers": {
                **{c: "TEXT" for c in ("app_script", "app_repo", "app_installed", "app_latest")},
                "type": "TEXT NOT NULL DEFAULT 'lxc'",  # lxc | qemu
                # where the app version comes from (app.apps.AppSource.kind), link, hint
                "app_kind": "TEXT",
                "app_url": "TEXT",
                "app_note": "TEXT",
            },
            "history": {
                "backup_kind": "TEXT",
                "backup_ref": "TEXT",
                "backup_removed": "INTEGER NOT NULL DEFAULT 0",
                "detail": "TEXT",
                "name": "TEXT",  # guest name when the job ran
            },
        }
        with self._conn:
            for table, cols in added.items():
                have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                for col, decl in cols.items():
                    if col not in have:
                        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            # entries from before the name was stored
            self._conn.execute(
                "UPDATE history SET name=(SELECT name FROM containers c WHERE c.vmid=history.vmid) WHERE name IS NULL"
            )

    def sync_containers(self, containers: list[dict]) -> None:
        """Upsert the container list from Proxmox and drop removed ones."""
        with self._conn:
            for c in containers:
                self._conn.execute(
                    """INSERT INTO containers (vmid, name, status, tags, type) VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(vmid) DO UPDATE SET name=excluded.name,
                       status=excluded.status, tags=excluded.tags, type=excluded.type""",
                    # host scripts before version 3 only listed containers, without "type"
                    (int(c["vmid"]), c.get("name", ""), c.get("status", ""), c.get("tags", ""),
                     c.get("type", "lxc")),
                )
            ids = [int(c["vmid"]) for c in containers]
            if ids:
                self._conn.execute(
                    f"DELETE FROM containers WHERE vmid NOT IN ({','.join('?' * len(ids))})", ids
                )
            else:
                self._conn.execute("DELETE FROM containers")

    def set_check_result(
        self, vmid: int, pkg_manager: str | None, community: bool, upgradable: list[str], error: str | None
    ) -> None:
        with self._conn:
            self._conn.execute(
                """UPDATE containers SET pkg_manager=COALESCE(?, pkg_manager), community_script=?,
                   upgradable=?, last_check=?, last_error=? WHERE vmid=?""",
                (pkg_manager, int(community), json.dumps(upgradable), time.time(), error, vmid),
            )

    def set_app_result(
        self, vmid: int, script: str | None, repo: str | None, installed: str | None, latest: str | None,
        kind: str | None = None, url: str | None = None, note: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """UPDATE containers SET app_script=?, app_repo=?, app_installed=?, app_latest=?,
                   app_kind=?, app_url=?, app_note=? WHERE vmid=?""",
                (script, repo, installed, latest, kind, url, note, vmid),
            )

    def containers(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM containers ORDER BY vmid").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["upgradable"] = json.loads(d["upgradable"])
            d["security"] = [p for p in d["upgradable"] if is_security(p)]
            d["community_script"] = bool(d["community_script"])
            d["tags"] = [t for t in d["tags"].split(";") if t]
            d["app_update"] = is_update(d["app_installed"], d["app_latest"])
            d["app_ahead"] = is_ahead(d["app_installed"], d["app_latest"])  # e.g. a pre-release
            result.append(d)
        return result

    def container(self, vmid: int) -> dict | None:
        return next((c for c in self.containers() if c["vmid"] == vmid), None)

    def start_history(self, vmid: int, kind: str, detail: str | None = None) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO history (vmid, kind, started, detail, name) "
                "VALUES (?, ?, ?, ?, (SELECT name FROM containers WHERE vmid=?))",
                (vmid, kind, time.time(), detail, vmid),
            )
        return cur.lastrowid

    def set_history_backup(self, history_id: int, kind: str, ref: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE history SET backup_kind=?, backup_ref=? WHERE id=?", (kind, ref, history_id)
            )

    def mark_snapshots_removed(self, vmid: int, names: list[str]) -> None:
        if not names:
            return
        with self._conn:
            self._conn.execute(
                f"""UPDATE history SET backup_removed=1 WHERE vmid=? AND backup_kind='snapshot'
                    AND backup_ref IN ({','.join('?' * len(names))})""",
                (vmid, *names),
            )

    def mark_backup_removed(self, vmid: int, ctime: int) -> None:
        """A vzdump backup was deleted: mark the update that made it (its ctime falls
        into the job's runtime)."""
        with self._conn:
            self._conn.execute(
                """UPDATE history SET backup_removed=1 WHERE vmid=? AND backup_kind='vzdump'
                   AND started <= ? + 60 AND COALESCE(finished, started) + 60 >= ?""",
                (vmid, ctime, ctime),
            )

    def mark_history_backup_removed(self, history_id: int) -> None:
        with self._conn:
            self._conn.execute("UPDATE history SET backup_removed=1 WHERE id=?", (history_id,))

    def mark_all_snapshots_removed(self, vmid: int) -> None:
        """A restore from a vzdump backup deletes all snapshots of the guest."""
        with self._conn:
            self._conn.execute(
                "UPDATE history SET backup_removed=1 WHERE vmid=? AND backup_kind='snapshot'", (vmid,)
            )

    def mark_vzdump_pruned(self, vmid: int, storage: str, keep: int) -> None:
        """The host deletes all but the newest <keep> marked backups; mirror that here."""
        with self._conn:
            self._conn.execute(
                """UPDATE history SET backup_removed=1 WHERE id IN (
                     SELECT id FROM history WHERE vmid=? AND backup_kind='vzdump' AND backup_ref=?
                     AND backup_removed=0 ORDER BY id DESC LIMIT -1 OFFSET ?)""",
                (vmid, storage, keep),
            )

    def history_entry(self, history_id: int) -> dict | None:
        row = self._conn.execute(
            f"SELECT {HISTORY_COLS} FROM history h LEFT JOIN containers c USING (vmid) WHERE h.id=?",
            (history_id,),
        ).fetchone()
        return dict(row) if row else None

    def finish_history(self, history_id: int, success: bool, log: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE history SET finished=?, success=?, log=? WHERE id=?",
                (time.time(), int(success), log, history_id),
            )

    def history(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            f"SELECT {HISTORY_COLS} FROM history h LEFT JOIN containers c USING (vmid) ORDER BY h.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_history(self, history_id: int) -> bool:
        """Remove one finished entry; running jobs (finished IS NULL) stay."""
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM history WHERE id=? AND finished IS NOT NULL", (history_id,)
            )
        return cur.rowcount > 0

    def clear_history(self) -> int:
        """Remove all finished entries, returns how many."""
        with self._conn:
            cur = self._conn.execute("DELETE FROM history WHERE finished IS NOT NULL")
        return cur.rowcount

    def history_log(self, history_id: int) -> str | None:
        row = self._conn.execute("SELECT log FROM history WHERE id=?", (history_id,)).fetchone()
        return row["log"] if row else None
