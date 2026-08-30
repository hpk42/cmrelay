"""System users and groups, created the way cmdeploy creates them.

Same names, because the maildir root is shared with whatever ran before and
because chatmaild and the units refer to them by name. cmdeploy pins no
numeric IDs, so vmail has a different uid on every host and the mailboxes are
owned by that host's particular number. Hence: never create a user that
exists, resolve and reuse its uid; create the genuinely absent ones the way
cmdeploy would; and never chown the mailbox tree, which on a mismatch is
destructive.
"""

import grp
import pwd
import shutil
import subprocess
from pathlib import Path

# (user, primary group, secondary groups), matching relay's required_users.
REQUIRED_USERS = [
    ("vmail", "vmail", []),
    ("iroh", None, []),
    ("opendkim", "opendkim", []),
    # postfix needs group access to the DKIM socket
    ("postfix", None, ["opendkim"]),
    # relay's nginx.conf says `user www-data`, so the name is not ours
    # to choose even on distributions that would call it something else
    ("www-data", "www-data", []),
    ("unbound", "unbound", []),
]

# Named by relay's configuration but declared by no deployer, because Debian's
# packages create them. dovecot will not even parse a config whose
# default_login_user does not resolve.
PACKAGE_USERS = [
    ("dovecot", "dovecot", []),
    ("dovenull", "dovenull", []),
]

ALL_USERS = REQUIRED_USERS + PACKAGE_USERS


class UserError(Exception):
    pass


def user_exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def group_exists(name: str) -> bool:
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def _tool(*candidates) -> str:
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    raise UserError(
        f"none of {', '.join(candidates)} found on PATH;"
        " cmrelay needs the host's user administration tools"
    )


def ensure_group(name: str) -> bool:
    if group_exists(name):
        return False
    subprocess.run([_tool("groupadd"), "--system", name], check=True)
    return True


def ensure_user(name: str, group: str | None, groups: list[str]) -> bool:
    """Create a system user the way `server.user(..., system=True)` does."""
    for extra in groups:
        ensure_group(extra)
    if group:
        ensure_group(group)

    if user_exists(name):
        if groups:
            subprocess.run(
                [_tool("usermod"), "-a", "-G", ",".join(groups), name], check=True
            )
        return False

    argv = [_tool("useradd"), "--system"]
    if group:
        argv += ["-g", group]
    if groups:
        argv += ["-G", ",".join(groups)]
    argv += ["-M", "-s", "/usr/sbin/nologin", name]
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode:
        # Not every distribution ships /usr/sbin/nologin at that path.
        argv[-3:-1] = ["-s", "/bin/false"]
        subprocess.run(argv, check=True)
    return True


def ensure_required_users() -> list[str]:
    """Create every user relay's deployers declare. Returns the ones created."""
    created = []
    for name, group, groups in ALL_USERS:
        if ensure_user(name, group, groups):
            created.append(name)
    return created


def verify_mailbox_ownership(mailboxes_dir: Path, user: str = "vmail") -> None:
    """Refuse to run if the resolved vmail does not own the maildir root.

    The signature of a restored backup or a hand-modified host, which is
    exactly where continuing would do damage.
    """
    path = Path(mailboxes_dir)
    if not path.exists():
        return
    want = pwd.getpwnam(user).pw_uid
    found = path.stat().st_uid
    if found != want:
        try:
            owner = pwd.getpwuid(found).pw_name
        except KeyError:
            owner = f"uid {found}"
        raise UserError(
            f"{path} is owned by {owner}, but cmrelay resolved {user!r} to"
            f" uid {want}. Refusing to continue: this host's mailboxes were"
            " created under a different account, and cmrelay never chowns the"
            " mailbox tree."
        )
