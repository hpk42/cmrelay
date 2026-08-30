"""Where cmrelay puts things.

The split is by kind, not by depth: everything cmrelay defines lives under
/opt/cmrelay, everything holding live state stays where it already is, which
is what makes a host convertible back to cmdeploy.

The store is the exception. Store paths are baked into every binary as its
ELF interpreter and RPATH and hash into every derivation, so the paths stay
and the bytes move: /nix is a symlink to /opt/cmrelay/nix.
"""

from pathlib import Path

NIX_ROOT = Path("/nix")

ROOT = Path("/opt/cmrelay")
INI = ROOT / "chatmail.ini"
CONFIG = ROOT / "config"
UNITS = ROOT / "systemd"
STATE = ROOT / "state"

# Disabled and masked units want opposite treatment on rollback: these want
# enabling again, DISABLED_UNITS wants unmasking.
SHADOWED_UNITS_STATE = STATE / "shadowed-units"

# The unmask is the load-bearing part of rollback: `systemctl enable` fails on
# a masked unit, so a `cmdeploy run` against leftover masks would not come up.
DISABLED_UNITS = STATE / "disabled-units"

LINKED_TOOLS = STATE / "linked-tools"

# sha256 per file cmrelay wrote outside its own tree, to spot hand edits.
WRITTEN = STATE / "written.json"

# What the operator asked for and chatmail.ini cannot express.
INTENT = STATE / "intent.json"

# Held for the whole of any verb that writes.
LOCK = STATE / "lock"

# What deploy last published under /var/www/html, so stale pages go.
WWW_FILES = STATE / "www-files"

MIGRATIONS = STATE / "migrations"
SIGNATURE = STATE / "signature.json"

NIX = ROOT / "nix"
STORE = NIX / "store"
STAGING = ROOT / ".staging"

# The operator's page source, and the release's own copy to diff against.
WWW_SRC = ROOT / "www"
WWW_DIST = ROOT / "www.dist"

# One directory per installed closure, plus a `current` symlink.
GENERATIONS = ROOT / "generations"
CURRENT = GENERATIONS / "current"

CGI_BIN = ROOT / "cgi-bin"

BIN = ROOT / "bin"
CLI = BIN / "cmrelay"
UNBOUND_ANCHOR_SEED = BIN / "unbound-seed-anchor"

# relay's remote tooling and test suite invoke these by bare name, so a store
# path is not enough.
LOCAL_BIN = Path("/usr/local/bin")

# Not shipped: the operator installs it, at the path relay's own deployer uses.
MTAIL = LOCAL_BIN / "mtail"

PROFILE_D = Path("/etc/profile.d")
PROFILE_SCRIPT = PROFILE_D / "cmrelay.sh"

# Where systemd loads from, and therefore where the real unit files go. See
# cmrelay.systemd for why they cannot live under /opt/cmrelay.
SYSTEMD_SYSTEM = Path("/etc/systemd/system")

DKIM_KEYS = Path("/etc/dkimkeys")
ACME_STATE = Path("/var/lib/acme")
WWW_ROOT = Path("/var/www/html")
POSTFIX_SPOOL = Path("/var/spool/postfix")

VERSION_MARKER = Path("/etc/chatmail-version")
MAILNAME = Path("/etc/mailname")
CMDEPLOY_INI = Path("/usr/local/lib/chatmaild/chatmail.ini")

# Host paths cmrelay reads or writes. Named here rather than inline so that
# this module is the whole list of what a deploy can touch.
RESOLV_CONF = Path("/etc/resolv.conf")
ENVIRONMENT = Path("/etc/environment")
ALIASES = Path("/etc/aliases")
ALIASES_DB = Path("/etc/aliases.db")
SYSCTL_INOTIFY = Path("/etc/sysctl.d/60-cmrelay-inotify.conf")
PROC_INOTIFY = Path("/proc/sys/fs/inotify")
JOURNALD_CONF = Path("/etc/systemd/journald.conf")
UNBOUND_STATE = Path("/var/lib/unbound")
UNBOUND_DROPINS = Path("/etc/unbound/unbound.conf.d")
UNBOUND_ANCHOR = UNBOUND_STATE / "root.key"
OPENDKIM_ETC = Path("/etc/opendkim")
NGINX_LIB = Path("/var/lib/nginx")
NGINX_LOG = Path("/var/log/nginx")
POSTFIX_LIB = Path("/var/lib/postfix")

# Debian patches acmetool to read this; upstream reads /usr/libexec/acme/hooks.
ACME_HOOKS = Path("/etc/acme/hooks")
UPSTREAM_ACME_HOOK = Path("/usr/lib/acme/hooks/nginx")

# Written by older chatmail deployments and removed by every deploy.
LEGACY = [
    Path("/etc/cron.d/expunge"),
    Path("/etc/cron.d/chatmail-metrics"),
    Path("/etc/cron.d/acmetool"),
    Path("/etc/mta-sts-daemon.yml"),
    Path("/etc/opendkim/screen.lua"),
    Path("/etc/opendkim/final.lua"),
]
LEGACY_DIRS = [Path("/usr/local/lib/postfix-mta-sts-resolver")]


def unit_file(name: str) -> Path:
    return SYSTEMD_SYSTEM / name


def config_dir(component: str) -> Path:
    return CONFIG / component
