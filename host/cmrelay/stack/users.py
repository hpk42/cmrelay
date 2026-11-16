"""System users and groups; no uid is pinned, so an existing account keeps its own."""

import subprocess

from .. import CmrelayError, paths

# The names the relay's configuration files use, as on a cmdeploy relay.
SYSUSERS = """\
u vmail - - /home/vmail
u iroh -
u opendkim -
u postfix -
# postfix needs group access to the DKIM socket
m postfix opendkim
g postdrop -
# nginx.conf says `user www-data`
u www-data -
u unbound -
u dovecot -
u dovenull -
"""


def ensure(changes) -> None:
    """Declare the accounts and create the absent ones."""
    changes.write("cmrelay", paths.SYSUSERS, changes.marked(SYSUSERS))
    argv = ["systemd-sysusers", str(paths.SYSUSERS)]
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode:
        raise CmrelayError(f"systemd-sysusers failed:\n{result.stderr.strip()}")
