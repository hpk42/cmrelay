"""Unit files, and the systemctl calls that make them real.

Real files go in /etc/systemd/system, indexed by symlinks under
/opt/cmrelay/systemd. The reverse does not survive contact with systemd:
`systemctl disable` removes a unit that is itself a symlink in the search
path, so disabling chatmail-expire.service would delete its own installation.

Unit names are a contract. The acceptance suite tails `journalctl -u dovecot`
and `-t postfix/smtpd`, which is also why the preflight exists: identical
names are what makes the distribution's own packages collide.
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
        # A mask (-> /dev/null) or an older layout; either way it must go
        # before a real file can take the name.
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


def ensure_service(
    name: str, running=True, enabled=True, restart=False, reload: bool = True
) -> None:
    if not available():
        return
    if enabled:
        systemctl("enable", *_no_reload(reload), name, check=False)
    else:
        systemctl("disable", *_no_reload(reload), name, check=False)
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


def _no_reload(reload: bool) -> tuple:
    """`--no-reload`, when the caller will reload once at the end itself.

    A reload costs 391 ms on a two-core VPS against about 20 ms for the
    symlink work, and a take-over touches thirteen units two or three times
    each. Nothing reads the unit graph until activate() reloads.
    """
    return ("--no-reload",) if not reload else ()


def stop_disable_mask(name: str, reload: bool = True) -> None:
    """Take a unit out of the way for good.

    Disable alone loses to the next apt or dnf upgrade re-enabling it.
    """
    flags = _no_reload(reload)
    systemctl("stop", name, check=False)
    systemctl("disable", *flags, name, check=False)
    systemctl("mask", *flags, name, check=False)
    # An already-failed unit stays in `systemctl --failed` after masking, so
    # a converted host would report a fault it no longer has.
    systemctl("reset-failed", name, check=False)


def tmpfiles_create() -> bool:
    """Recreate the /run directories the distribution's packages declare.

    systemd deletes a RuntimeDirectory= when its service stops. On a
    taken-over host that same path usually belongs to tmpfiles instead
    (Debian's opendkim.service relies on it), so stopping cmrelay's unit
    leaves the distribution's unit without a directory it never creates.
    This is the next reboot's effect, without the reboot.
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


def unmask(name: str, reload: bool = True) -> None:
    systemctl("unmask", *_no_reload(reload), name, check=False)


def enable(name: str, reload: bool = True) -> None:
    systemctl("enable", *_no_reload(reload), name, check=False)


def read_units(state: Path) -> list[str]:
    if not state.is_file():
        return []
    return [line.strip() for line in state.read_text().splitlines() if line.strip()]


def record_units(state: Path, names) -> None:
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("\n".join(sorted(set(read_units(state)) | set(names))) + "\n")
