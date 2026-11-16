"""unbound, the relay's own resolver, and the resolv.conf that points at it."""

import shutil
from pathlib import Path

from ... import paths
from ...system import fsops, systemd

CONF_DIR = paths.CONFIG / "unbound"
STATE = Path("/var/lib/unbound")
DROPINS = Path("/etc/unbound/unbound.conf.d")
ANCHOR = STATE / "root.key"
ANCHOR_SEED = paths.BIN / "unbound-seed-anchor"
RESOLV_CONF = Path("/etc/resolv.conf")
# what /etc/resolv.conf was before a deploy pointed it at unbound; uninstall restores it
RESOLV_CONF_SAVED = paths.STATE / "resolv.conf"
# a file of the same name under SYSTEMD_SYSTEM shadows netplan's drop-in
NETPLAN_WAIT_ONLINE = Path(
    "/run/systemd/generator.late/systemd-networkd-wait-online.service.d/10-netplan.conf"
)

# Compete with unbound for /etc/resolv.conf and cannot be purged, so masked.
RESOLVER_UNITS = [
    "systemd-resolved.service",
    # since systemd 258; left listening, the first client fails them
    "systemd-resolved-monitor.socket",
    "systemd-resolved-varlink.socket",
    "unbound-resolvconf.service",
]


def first_nameserver() -> str:
    if not RESOLV_CONF.is_file():
        return ""
    for line in RESOLV_CONF.read_text(errors="replace").splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) >= 2 and fields[0] == "nameserver":
            return fields[1]
    return ""


def configure(deploy):
    configure_resolver(deploy)
    # Seeds the root trust anchor when there is none (RFC 5011).
    deploy.changes.write(
        "unbound",
        ANCHOR_SEED,
        "#!/bin/sh\n"
        "# written by cmrelay; regenerated on every run\n"
        f"test -s {ANCHOR} && exit 0\n"
        f"exec {deploy.bin('unbound', 'unbound-anchor')} -a {ANCHOR}\n",
        mode=0o755,
    )
    drop_in_dir = CONF_DIR / "unbound.conf.d"
    deploy.put_template(
        "unbound",
        "stack/unbound/unbound.conf.j2",
        drop_in_dir / "chatmail.conf",
        disable_ipv6=deploy.config.disable_ipv6,
    )
    deploy.changes.ensure_directory("unbound", STATE, owner=("unbound", "unbound"))
    deploy.changes.ensure_directory("unbound", DROPINS)

    # Two anchors for one zone is fatal; yield to Debian's rather than remove it.
    anchor_line = f'    auto-trust-anchor-file: "{ANCHOR}"\n'
    keys = ("auto-trust-anchor-file:", "trust-anchor-file:")
    confs = sorted(DROPINS.glob("*.conf"))
    lines = (s for c in confs for s in c.read_text(errors="replace").splitlines())
    if any(line.strip().startswith(keys) for line in lines):
        anchor_line = ""
    # A store unbound lacks the top-level unbound.conf.
    deploy.changes.write(
        "unbound",
        CONF_DIR / "unbound.conf",
        "# written by cmrelay; regenerated on every run\n"
        "server:\n"
        '    username: "unbound"\n'
        '    directory: "/var/lib/unbound"\n'
        '    chroot: ""\n'
        '    pidfile: "/run/unbound.pid"\n' + anchor_line + "\n"
        f'include: "{drop_in_dir}/*.conf"\n'
        f'include: "{DROPINS}/*.conf"\n',
    )
    deploy.put_format_unit(
        "unbound",
        "stack/unbound/unbound.service",
        unbound_bin=deploy.bin("unbound"),
        unbound_anchor_seed=str(ANCHOR_SEED),
        unbound_config=str(CONF_DIR / "unbound.conf"),
    )


def configure_resolver(deploy):
    """Point resolv.conf at unbound, and keep netplan from waiting for resolved."""
    if first_nameserver() != "127.0.0.1":
        # kept for uninstall: a link, such as Ubuntu's resolved stub, stays one
        saved, resolv = RESOLV_CONF_SAVED, RESOLV_CONF
        if not (saved.is_symlink() or saved.exists()):
            if resolv.is_symlink():
                deploy.changes.symlink("unbound", saved, resolv.readlink())
            elif resolv.is_file():
                deploy.changes.copy("unbound", resolv, saved)
        # trailing newline: a SolusVM bug needs it
        content = "nameserver 127.0.0.1\nnameserver 9.9.9.9\n"
        deploy.changes.write("unbound", RESOLV_CONF, content)
    # netplan's --dns waits for the masked systemd-resolved: every unit wanting
    # network-online.target would hang for two minutes, and the boot end degraded
    netplan = NETPLAN_WAIT_ONLINE
    if netplan.is_file() and " --dns" in (text := netplan.read_text()):
        note = "# netplan's own, without --dns\n"
        unit = "systemd-networkd-wait-online.service"
        deploy.changes.install_dropin(
            "unbound", unit, netplan.name, note + text.replace(" --dns", "")
        )


def restore_resolv_conf(out):
    """Unbound goes, so resolv.conf gets back what the deploy replaced."""
    saved = RESOLV_CONF_SAVED
    if first_nameserver() != "127.0.0.1" or not (saved.is_symlink() or saved.exists()):
        return
    out.section("Giving back /etc/resolv.conf")
    # moved, so a link such as Ubuntu's resolved stub comes back as one
    RESOLV_CONF.unlink(missing_ok=True)
    shutil.move(saved, RESOLV_CONF)
    out.print(f"  restored {RESOLV_CONF}")
    # its stub answers only while resolved runs, and uninstall starts nothing else
    if "unmask systemd-resolved.service" in fsops.read_lines(paths.UNDO):
        systemd.daemon_reload()  # it was unmasked with --no-reload
        systemd.must("start", "systemd-resolved.service")
        out.print("  started systemd-resolved.service")


def units(deploy) -> list:
    return [("unbound.service", True, "unbound")]
