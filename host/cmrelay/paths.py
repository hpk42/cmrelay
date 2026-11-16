"""Where cmrelay puts things: the operator's files in /opt/cmrelay, the rest in lib/."""

from pathlib import Path

ROOT = Path("/opt/cmrelay")
INI = ROOT / "chatmail.ini"
WWW_SRC = ROOT / "www-src"
LIB = ROOT / "lib"
CONFIG = LIB / "config"
STORE = LIB / "store"
STAGING = LIB / ".staging"
BIN = LIB / "bin"
CLI = BIN / "cmrelay"

STATE = LIB / "state"
# the store paths of the installed closure; a deploy removes the others
STORE_PATHS = STATE / "store-paths"
# what uninstall runs to give back the units cmrelay retired: "unmask NAME", "enable NAME"
UNDO = STATE / "undo"
# digest of the chatmail.ini the daemons last started with
INI_DIGEST = STATE / "chatmail.ini.sha256"
LOCK = STATE / "lock"
# present while a deploy runs; left behind, the next one restarts everything
DEPLOY_PENDING = STATE / "deploy-pending"

LOCAL_BIN = Path("/usr/local/bin")
# not shipped; the operator installs it
MTAIL = LOCAL_BIN / "mtail"

SYSTEMD_SYSTEM = Path("/etc/systemd/system")

WWW_ROOT = Path("/var/www/html")
VERSION_MARKER = Path("/etc/chatmail-version")
CMDEPLOY_INI = Path("/usr/local/lib/chatmaild/chatmail.ini")
SYSUSERS = Path("/etc/sysusers.d/cmrelay.conf")
SYSCTL_INOTIFY = Path("/etc/sysctl.d/60-cmrelay-inotify.conf")
JOURNALD_DROPINS = Path("/etc/systemd/journald.conf.d")
# Debian patches acmetool to read this; upstream reads /usr/libexec/acme/hooks.
ACME_HOOKS = Path("/etc/acme/hooks")


# the start of the first line, after any shebang, of every file cmrelay writes
# outside /opt/cmrelay; a deploy adds its version, and uninstall removes what has it
MARKER = "# written by cmrelay"


def written_by_cmrelay(path: Path) -> bool:
    """A link into /opt/cmrelay, or a file whose head carries the marker."""
    if path.is_symlink():
        return str(path.readlink()).startswith(str(ROOT))
    if path.is_file():
        with path.open("rb") as f:
            return MARKER.encode() in f.read(200)
    return False


def own_units() -> list:
    """The names of the unit files cmrelay wrote."""
    units = (p for p in SYSTEMD_SYSTEM.glob("*") if not p.is_symlink())
    return sorted(p.name for p in units if written_by_cmrelay(p))


def outside_files() -> list:
    """What cmrelay wrote outside /opt/cmrelay, apart from data and /etc/chatmail-version."""
    dirs = [SYSTEMD_SYSTEM, LOCAL_BIN, JOURNALD_DROPINS, ACME_HOOKS]
    dirs += [SYSUSERS.parent, SYSCTL_INOTIFY.parent]
    globs = [SYSTEMD_SYSTEM.glob("*.d/*"), *(d.glob("*") for d in dirs)]
    return sorted(p for g in globs for p in g if written_by_cmrelay(p))


def owned_unit(name: str) -> bool:
    """Whether this unit name is already served by a cmrelay unit file."""
    unit = SYSTEMD_SYSTEM / name
    return not unit.is_symlink() and written_by_cmrelay(unit)
