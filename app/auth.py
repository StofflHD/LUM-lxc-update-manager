"""Single-admin login for the web UI.

- Credentials live in data/auth.json (username + scrypt hash), managed with
  `python -m app.passwd`. Re-read when the file changes, no restart needed.
- Sessions are stateless signed cookies: base64("user|expires|sig"). The
  signature covers a fingerprint of the password hash, so changing the
  password logs out every session.
- Failed logins are rate limited per client IP.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

COOKIE = "lum_session"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}

MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"scrypt:{_SCRYPT['n']}:{_SCRYPT['r']}:{_SCRYPT['p']}:{b64(salt)}:{b64(digest)}"


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_hash(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split(":")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), dklen=32, n=int(n), r=int(r), p=int(p)
        )
        return hmac.compare_digest(calc, _unb64(digest))
    except (ValueError, TypeError):
        return False


def write_credentials(path: Path, username: str, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"username": username, "hash": hash_password(password)}))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


class Auth:
    def __init__(self, auth_file: Path, secret_file: Path, session_hours: int):
        self._file = auth_file
        self._session_seconds = session_hours * 3600
        self._secret = self._load_secret(secret_file)
        self._cache: tuple[float, dict | None] = (-1.0, None)
        self._failures: dict[str, deque] = defaultdict(deque)

    @staticmethod
    def _load_secret(path: Path) -> bytes:
        if path.exists():
            return path.read_bytes()
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        path.write_bytes(key)
        os.chmod(path, 0o600)
        return key

    # --- credentials -------------------------------------------------------

    def credentials(self) -> dict | None:
        try:
            mtime = self._file.stat().st_mtime
        except FileNotFoundError:
            return None
        if mtime != self._cache[0]:
            try:
                data = json.loads(self._file.read_text())
                creds = data if data.get("username") and data.get("hash") else None
            except (OSError, ValueError):
                creds = None
            self._cache = (mtime, creds)
        return self._cache[1]

    @property
    def configured(self) -> bool:
        return self.credentials() is not None

    def check_password(self, username: str, password: str) -> bool:
        creds = self.credentials()
        if not creds:
            return False
        # always run scrypt, so a wrong username takes as long as a wrong password
        ok_pw = verify_hash(password, creds["hash"])
        return hmac.compare_digest(username.encode(), creds["username"].encode()) and ok_pw

    def change_password(self, new_password: str) -> None:
        creds = self.credentials()
        write_credentials(self._file, creds["username"], new_password)
        self._cache = (-1.0, None)  # don't rely on mtime resolution

    # --- rate limiting -----------------------------------------------------

    def locked_for(self, ip: str) -> int:
        """Seconds until <ip> may try again, 0 if not locked."""
        q = self._failures[ip]
        now = time.time()
        while q and q[0] < now - LOCKOUT_SECONDS:
            q.popleft()
        if len(q) >= MAX_FAILURES:
            return int(q[0] + LOCKOUT_SECONDS - now) + 1
        return 0

    def record_failure(self, ip: str) -> None:
        self._failures[ip].append(time.time())

    def reset_failures(self, ip: str) -> None:
        self._failures.pop(ip, None)

    # --- sessions ----------------------------------------------------------

    def _sign(self, username: str, expires: int) -> str:
        creds = self.credentials() or {}
        fingerprint = hashlib.sha256(creds.get("hash", "").encode()).hexdigest()[:16]
        msg = f"{username}|{expires}|{fingerprint}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def make_token(self, username: str) -> str:
        expires = int(time.time()) + self._session_seconds
        raw = f"{username}|{expires}|{self._sign(username, expires)}"
        return base64.urlsafe_b64encode(raw.encode()).decode()

    def check_token(self, token: str | None) -> str | None:
        """Username of a valid session, else None."""
        if not token or not self.configured:
            return None
        try:
            username, expires_s, sig = base64.urlsafe_b64decode(token.encode()).decode().rsplit("|", 2)
            expires = int(expires_s)
        except (ValueError, UnicodeDecodeError):
            return None
        if expires < time.time():
            return None
        if not hmac.compare_digest(sig, self._sign(username, expires)):
            return None
        if username != self.credentials()["username"]:
            return None
        return username

    @property
    def session_seconds(self) -> int:
        return self._session_seconds
