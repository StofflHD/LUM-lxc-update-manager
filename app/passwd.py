"""Set the web UI login.

    python -m app.passwd [username]          asks for the password twice
    echo 'pw' | python -m app.passwd --stdin [username]

Run from the install directory (/opt/lxc-update-manager) with the venv python.
Takes effect immediately and logs out all existing sessions.
"""

import getpass
import re
import sys

from .auth import write_credentials
from .config import get_settings

MIN_LENGTH = 8


def main(argv: list[str]) -> int:
    use_stdin = "--stdin" in argv
    args = [a for a in argv if a != "--stdin"]
    username = args[0] if args else "admin"
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", username):
        print("Username: only letters, digits, . _ - (max. 40 characters).", file=sys.stderr)
        return 1

    if use_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass(f"New password for '{username}': ")
        if password != getpass.getpass("Repeat: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1

    if len(password) < MIN_LENGTH:
        print(f"Password must have at least {MIN_LENGTH} characters.", file=sys.stderr)
        return 1

    path = get_settings().auth_file
    write_credentials(path, username, password)
    print(f"Login for '{username}' saved ({path}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
