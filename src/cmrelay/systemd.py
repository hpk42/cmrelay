"""Unit files, and the systemctl calls that make them real.

cmrelay writes the real unit files into /etc/systemd/system and keeps a
directory of symlinks to them under /opt/cmrelay/systemd, so `ls` there still
shows exactly cmrelay's units and following any of them lands on the file
systemd loads.

The obvious arrangement is the other way round -- real files under
/opt/cmrelay, symlinked into /etc/systemd/system -- and it does not survive
contact with systemd. `systemctl disable` removes a unit that is itself a
symlink in the unit search path, so disabling `chatmail-expire.service`
(which cmrelay does deliberately, because its timer owns it) would delete the
installation of that unit, and the timer would then refuse to start against a
target that is no longer loaded. Real files are not removed by `disable`.

Unit names are a contract, not a coincidence. The acceptance suite tails
`journalctl -u dovecot` and `-t postfix/smtpd`, so renaming dovecot.service
to cmrelay-dovecot.service would silently cost the entire oracle. It is also
why the preflight exists: identical names are exactly what makes the
distribution's own packages collide.
"""

import shutil
import subprocess
from pathlib import Path

from . import paths
from .render import write


class SystemdError(Exception):
    pass


def available() -> bool:
    """False during container image builds and any other non-systemd environment."""
    return Path("/run/systemd/system").is_dir()


def systemctl(*args, check=True, capture=True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", *args],
        capture_output=capture,
        text=True,
        check=check,
    )


def query(*args) -> str:
    return systemctl(*args, check=False).stdout.strip()


def install_unit(name: str, content: str) -> bool:
    """Write a unit where systemd loads it, and index it under /opt/cmrelay."""
    real = paths.unit_file(name)
    if real.is_symlink():
        # Either a mask (-> /dev/null) or an older cmrelay layout. Both have
        # to go before a real file can take the name.
        real.unlink()

    changed = write(real, content, mode=0o644)

    index = paths.UNITS / name
    index.parent.mkdir(parents=True, exist_ok=True)
    if index.is_symlink() or index.exists():
        index.unlink()
    index.symlink_to(real)
    return changed


def install_dropin(unit: str, name: str, content: str) -> bool:
    """Drop-ins have to be real files: systemd does not follow a symlinked dir."""
    return write(paths.SYSTEMD_SYSTEM / f"{unit}.d" / name, content, mode=0o644)


def daemon_reload() -> None:
    if available():
        systemctl("daemon-reload")


def ensure_service(name: str, running=True, enabled=True, restart=False) -> None:
    if not available():
        return
    if enabled:
        systemctl("enable", name, check=False)
    else:
        systemctl("disable", name, check=False)
    if running:
        verb = "restart" if restart else "start"
        result = systemctl(verb, name, check=False)
        if result.returncode:
            raise SystemdError(
                f"systemctl {verb} {name} failed:\n{result.stderr.strip()}\n"
                f"{query('status', '--no-pager', '--lines=20', name)}"
            )
    else:
        systemctl("stop", name, check=False)


def stop_disable_mask(name: str) -> None:
    """Take a unit out of the way for good.

    Disable alone loses to the next apt or dnf upgrade re-enabling the unit,
    which is why cmdeploy masks systemd-resolved rather than disabling it.
    """
    systemctl("stop", name, check=False)
    systemctl("disable", name, check=False)
    systemctl("mask", name, check=False)
    # A unit that was already failed stays in `systemctl --failed` after
    # masking, so a converted host reports a fault it no longer has and an
    # operator cannot tell it from one that matters.
    systemctl("reset-failed", name, check=False)


def tmpfiles_create() -> bool:
    """Recreate the /run directories the distribution's packages declare.

    systemd deletes a RuntimeDirectory= when its service stops, and cmrelay's
    units declare one because a fresh host has no package to declare it for
    them. On a host taken over from a distribution the same path is usually
    tmpfiles' -- Debian's opendkim.service has no RuntimeDirectory and relies
    on /usr/lib/tmpfiles.d/opendkim.conf creating /run/opendkim at boot -- so
    stopping cmrelay's unit removes a directory the distribution's unit needs
    and does not create. `cmdeploy run` then fails on

        can't write pid to /run/opendkim/opendkim.pid: No such file or directory

    until the next reboot, which is when tmpfiles would run anyway. This is
    that reboot's effect, without the reboot.
    """
    if not shutil.which("systemd-tmpfiles"):
        return False
    subprocess.run(
        ["systemd-tmpfiles", "--create"],
        capture_output=True,
        text=True,
        check=False,
    )
    return True


def unmask(name: str) -> None:
    systemctl("unmask", name, check=False)


def read_disabled_units() -> list[str]:
    if not paths.DISABLED_UNITS.is_file():
        return []
    return [
        line.strip()
        for line in paths.DISABLED_UNITS.read_text().splitlines()
        if line.strip()
    ]


def read_shadowed_units() -> list[str]:
    if not paths.SHADOWED_UNITS_STATE.is_file():
        return []
    return [
        line.strip()
        for line in paths.SHADOWED_UNITS_STATE.read_text().splitlines()
        if line.strip()
    ]


def record_shadowed_units(names) -> None:
    paths.SHADOWED_UNITS_STATE.parent.mkdir(parents=True, exist_ok=True)
    merged = sorted(set(read_shadowed_units()) | set(names))
    paths.SHADOWED_UNITS_STATE.write_text("\n".join(merged) + "\n")


def enable(name: str) -> None:
    systemctl("enable", name, check=False)


def record_disabled_units(names) -> None:
    paths.DISABLED_UNITS.parent.mkdir(parents=True, exist_ok=True)
    existing = read_disabled_units()
    merged = sorted(set(existing) | set(names))
    paths.DISABLED_UNITS.write_text("\n".join(merged) + "\n")
