"""System users and groups, created the way cmdeploy creates them.

cmrelay must create the same users and groups under the same names, because
it shares /home/vmail/mail/<domain> with whatever ran before it, and because
chatmaild and the units refer to them by name rather than by number:
config.get_user() returns User(..., uid="vmail", gid="vmail"), nine units
declare User=vmail and one declares User=iroh.

The sharp edge is that cmdeploy creates these with system=True and pins no
numeric IDs, so vmail has a different UID on every host and the mailboxes on
disk are owned by that host's particular number. Three rules follow, and the
third prevents a disaster:

- On upgrade, never create a user that exists. Resolve it and reuse the uid
  that is there. Creating a differently named user, or assuming a number,
  breaks access to every mailbox on the host. Upgrade does create the ones
  that are genuinely absent, because a host part way through a cmdeploy run
  may be missing some, and it reports which.

- On a bare install, create them exactly as cmdeploy does: same names,
  system-allocated. Matching that keeps a later `cmdeploy run` from
  conflicting.

- Never chown the mailbox tree. Read the ownership that is there and adapt.
  A recursive chown across a populated relay is slow at best and destructive
  on a mismatch.
"""

import grp
import pwd
import shutil
import subprocess
from pathlib import Path

# (user, primary group, secondary groups), matching the required_users
# declarations spread across relay's deployers.
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

# Accounts relay's configuration names but no deployer declares, because on
# Debian the distribution packages create them. cmrelay installs no packages,
# so these are its job: dovecot refuses to even parse a config whose
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


def check_all_present() -> list[str]:
    """Names that upgrade expected to find and did not."""
    return [name for name, _, _ in ALL_USERS if not user_exists(name)]


def verify_mailbox_ownership(mailboxes_dir: Path, user: str = "vmail") -> None:
    """Refuse to run if the resolved vmail does not own the maildir root.

    That mismatch is the signature of a restored backup or a hand-modified
    host, and it is exactly the case where continuing would do damage.
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
