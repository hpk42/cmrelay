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

NIX = ROOT / "nix"
STORE = NIX / "store"

CGI_BIN = ROOT / "cgi-bin"

BIN = ROOT / "bin"
CLI = BIN / "cmrelay"
UNBOUND_ANCHOR_SEED = BIN / "unbound-seed-anchor"

# relay's remote tooling and test suite invoke these by bare name, so a store
# path is not enough.
LOCAL_BIN = Path("/usr/local/bin")

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


def unit_file(name: str) -> Path:
    return SYSTEMD_SYSTEM / name


def config_dir(component: str) -> Path:
    return CONFIG / component
