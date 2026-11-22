"""`cmrelay uninstall`: hand the host back to `cmdeploy run`.

Only a resolver is started: anything else would race the teardown.
"""

import platform
import shutil

from .. import paths
from ..stack.unbound import unbound
from ..system import fsops, systemd


def uninstall_verb(args, bundle, out) -> int:
    if not out.confirm(f"Remove cmrelay from {platform.node()}?", args.yes):
        return 1

    out.section("Stopping cmrelay units")
    for name in paths.own_units():
        systemd.retire(name)
        out.print(f"  stopped {name}")

    # Enabled, not started: `cmdeploy run` resets failed units, which fails on a
    # disabled one, and starting them is its job.
    out.section("Giving back the units cmrelay masked or disabled")
    undo = [line.split() for line in fsops.read_lines(paths.UNDO)]
    # unmasked first: a masked unit cannot be enabled
    for verb, name in sorted(undo, key=lambda u: u[0] != "unmask"):
        systemd.systemctl(verb, "--no-reload", name)
        out.print(f"  systemctl {verb} {name}")

    systemd.tmpfiles_create()
    unbound.restore_resolv_conf(out)

    out.section("Removing what cmrelay wrote outside /opt/cmrelay")
    for path in paths.outside_files():
        path.unlink()
        out.print(f"  removed {path}")
        if path.parent.suffix == ".d" and not any(path.parent.iterdir()):
            path.parent.rmdir()

    out.section("Removing the tree")
    shutil.rmtree(paths.ROOT)
    out.print(f"  removed {paths.ROOT}")
    # without it the host classifies as bare again; the accounts stay
    paths.VERSION_MARKER.unlink(missing_ok=True)

    systemd.daemon_reload()
    # a taken-over relay's unbound, only now that cmrelay's own unit is gone
    reenabled = ["enable", "unbound.service"] in undo
    if reenabled and unbound.first_nameserver() == "127.0.0.1":
        systemd.must("start", "unbound.service")
        out.print("  started unbound.service")
    out.section("Done")
    out.print("You can now use `cmdeploy run` on the machine where you used to do it.")
    return 0
