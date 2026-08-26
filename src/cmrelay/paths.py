"""Where cmrelay puts things, and why the split falls where it does.

The rule is by kind, not by directory depth. Everything cmrelay *defines* --
chatmail.ini, the rendered configuration for every component, the systemd
units -- lives under /opt/cmrelay. Everything holding live state stays where
it already is: mail and credentials in the maildir root, the DKIM key in
/etc/dkimkeys, certificates in /var/lib/acme, sockets in /run.

That is what makes a cmrelay host convertible back to cmdeploy: nothing was
taken away, only which units are enabled changed.

The store is the one place where the path is not ours to choose. Store paths
are baked into every binary as its ELF interpreter and RPATH, and the store
directory is an input to every derivation hash, so building for
/opt/cmrelay/nix/store would mean building glibc and everything above it from
source. So keep the paths and move the bytes: /nix is a symlink to
/opt/cmrelay/nix.
"""

from pathlib import Path

# The one thing cmrelay owns outside its own tree. Uninstall is `rm /nix`
# followed by `rm -rf /opt/cmrelay`, which is also safer than deleting
# individual paths out of a store somebody else might share.
NIX_ROOT = Path("/nix")

ROOT = Path("/opt/cmrelay")

# cmrelay's source of truth. Created once and never overwritten, matching how
# `cmdeploy init` refuses to touch an existing ini without --recreate-ini.
INI = ROOT / "chatmail.ini"

# Rendered configuration, one directory per component.
CONFIG = ROOT / "config"

# An index of cmrelay's units: one symlink per unit, pointing at the real
# file in /etc/systemd/system. See cmrelay.systemd for why the real files
# cannot live here.
UNITS = ROOT / "systemd"

STATE = ROOT / "state"

# What `install --take-over` masked, so `uninstall` can unmask it. The unmask is the
# load-bearing part of rollback: `systemctl enable` fails on a masked unit,
# so a `cmdeploy run` against a host still carrying cmrelay's masks would not
# come back up.
DISABLED_UNITS = STATE / "disabled-units"

# What link_host_tools() put in /usr/local/bin, so uninstall removes
# exactly those and leaves anything else alone.
LINKED_TOOLS = STATE / "linked-tools"

NIX = ROOT / "nix"
STORE = NIX / "store"

# nginx runs newemail.py through fcgiwrap. cmdeploy puts it in
# /usr/lib/cgi-bin; cmrelay keeps its own copy in its own tree so the two
# installations never fight over one file.
CGI_BIN = ROOT / "cgi-bin"

# A stable name for the CLI. The closure path changes with every release,
# so anything scripting cmrelay -- an operator, cmlxc -- needs one that
# does not.
BIN = ROOT / "bin"
CLI = BIN / "cmrelay"

# See Deployment.write_unbound_anchor_seed for why this is not a bare
# unbound-anchor call in the unit.
UNBOUND_ANCHOR_SEED = BIN / "unbound-seed-anchor"

# Operator-facing tools, on the PATH they have on a Debian relay. relay's own
# remote tooling and its test suite invoke several of these by bare name --
# `dovecot --version`, `doveadm quota get`, `acmetool account-url` -- so a
# store path is not enough; the names have to be findable.
LOCAL_BIN = Path("/usr/local/bin")

# A login shell reads /etc/profile, which sources this directory on every
# distribution cmrelay supports. It is the one place a package may add to
# PATH without editing a file the distribution owns.
PROFILE_D = Path("/etc/profile.d")
PROFILE_SCRIPT = PROFILE_D / "cmrelay.sh"

# Where systemd looks, and therefore where cmrelay's real unit files live.
# See cmrelay.systemd for why they cannot live under /opt/cmrelay instead.
SYSTEMD_SYSTEM = Path("/etc/systemd/system")

# Shared in place with whatever ran before, never relocated and never copied.
DKIM_KEYS = Path("/etc/dkimkeys")
ACME_STATE = Path("/var/lib/acme")
WWW_ROOT = Path("/var/www/html")
POSTFIX_SPOOL = Path("/var/spool/postfix")

# The marker cmdeploy writes and its test suite reads. cmrelay writes it too,
# carrying the cmrelay version and the relay commit from vendor.lock.
VERSION_MARKER = Path("/etc/chatmail-version")

MAILNAME = Path("/etc/mailname")

# cmdeploy's own config path. cmrelay reads it once during --take-over and
# leaves it alone thereafter, so rollback finds it where it was.
CMDEPLOY_INI = Path("/usr/local/lib/chatmaild/chatmail.ini")


def unit_file(name: str) -> Path:
    """The real unit file, in the directory systemd loads from."""
    return SYSTEMD_SYSTEM / name


def config_dir(component: str) -> Path:
    return CONFIG / component
