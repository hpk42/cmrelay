"""cmrelay's only way into systemd."""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import CmrelayError, paths


@dataclass(frozen=True)
class UnitState:
    load: str
    active: str
    enabled: str

    @property
    def exists(self) -> bool:
        return self.load != "not-found"

    @property
    def is_running(self) -> bool:
        return self.active in ("active", "activating", "reloading")

    @property
    def is_up(self) -> bool:
        """Running, and not "activating": that is also how a restart loop looks."""
        return self.active in ("active", "reloading")

    @property
    def is_enabled(self) -> bool:
        """Already enabled, so an enable changes nothing."""
        states = ("enabled", "enabled-runtime", "static", "alias", "indirect")
        return self.enabled in states

    @property
    def is_masked(self) -> bool:
        return self.enabled == "masked" or self.load == "masked"


def available() -> bool:
    return Path("/run/systemd/system").is_dir() and bool(shutil.which("systemctl"))


def systemctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", *args], capture_output=True, text=True, check=False
    )


def query(*args) -> str:
    return systemctl(*args).stdout.strip()


def unit_states(names) -> dict:
    """name -> UnitState, for every name asked about, in one call."""
    # not `--value`: its empty lines make a not-found unit look like a boundary
    names = list(names)
    if not names:
        return {}
    if not available():
        raise CmrelayError("cmrelay needs systemd, and this host is not running it")
    show = query("show", "-p", "Id,LoadState,ActiveState,UnitFileState", *names)
    states = {}
    record = {}
    for line in [*show.splitlines(), ""]:
        if not line.strip():
            if record.get("Id"):
                states[record["Id"]] = UnitState(
                    record.get("LoadState", ""),
                    record.get("ActiveState", ""),
                    record.get("UnitFileState", ""),
                )
            record = {}
            continue
        key, _, value = line.partition("=")
        record[key] = value
    return states


def must(verb, *args) -> None:
    result = systemctl(verb, *args)
    if result.returncode:
        raise CmrelayError(f"systemctl {verb} failed:\n{result.stderr.strip()}")


def daemon_reload() -> None:
    must("daemon-reload")


def apply(units: dict, states: dict) -> list:
    """Bring *units* to what `Deployment.units` says, one call per verb; return (unit, verb) pairs."""
    units = {n: v for n, v in units.items() if n in states and states[n].exists}
    on = [n for n, (want, _) in units.items() if want]
    off = [n for n, (want, _) in units.items() if want is False]
    restart = [n for n, (want, again) in units.items() if again and want is not False]
    for verb, names in (("enable", on), ("disable", off)):
        if names:
            must(verb, "--no-reload", *names)
    for verb, names in (("stop", off), ("restart", restart), ("start", on)):
        result = systemctl(verb, *names) if names else None
        if verb != "stop" and result is not None and result.returncode:
            failed = [n for n, s in unit_states(names).items() if s.active == "failed"]
            raise CmrelayError(
                f"systemctl {verb} failed:\n{result.stderr.strip()}\n"
                f"{query('status', '--no-pager', '--lines=20', *(failed or names))}"
            )
    moved = [(n, "enable") for n in on if not states[n].is_enabled]
    disabling = ("enabled", "enabled-runtime")
    moved += [(n, "disable") for n in off if states[n].enabled in disabling]
    moved += [(n, "stop") for n in off if states[n].is_running]
    moved += [(n, "restart" if states[n].is_running else "start") for n in restart]
    moved += [(n, "start") for n in on if n not in restart and not states[n].is_running]
    return moved


def retire(name: str, mask: bool = False) -> bool:
    """Stop and disable a unit, and mask it if asked: apt re-enables a disabled one.

    Says whether it masked: a unit file of its own in /etc/systemd/system
    cannot be masked, and no package re-enables that one either.
    """
    must("stop", name)
    must("disable", "--no-reload", name)
    if not mask or (paths.SYSTEMD_SYSTEM / name).is_file():
        return False
    must("mask", "--no-reload", name)
    # A failed unit would stay in `systemctl --failed` after masking.
    systemctl("reset-failed", name)
    return True


def tmpfiles_create() -> None:
    """Recreate the /run directories systemd removed when cmrelay's units stopped."""
    subprocess.run(["systemd-tmpfiles", "--create"], capture_output=True, check=False)
