"""What systemd is doing, and the smallest set of calls that changes it.

Unit files belong to `cmrelay.fsops`; this module only asks and tells. State
is read once into a snapshot, the planner compares intent against it, and
only the units that differ are touched. `systemctl enable` costs 158 ms with
its implicit daemon-reload against 8 ms with `--no-reload`, and one
`systemctl show` costs 50 ms against 222 ms for per-unit queries.

Unit names are a contract: the acceptance suite tails `journalctl -u dovecot`
and `-t postfix/smtpd`.
"""

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

# States meaning "already enabled", so enabling again would spend a process.
ENABLED_STATES = ("enabled", "enabled-runtime", "static", "alias", "indirect")
RUNNING_STATES = ("active", "activating", "reloading")


class SystemdError(Exception):
    pass


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
        return self.active in RUNNING_STATES

    @property
    def is_enabled(self) -> bool:
        return self.enabled in ENABLED_STATES

    @property
    def is_masked(self) -> bool:
        return self.enabled == "masked" or self.load == "masked"


def available() -> bool:
    """False during container image builds and any other non-systemd environment."""
    return Path("/run/systemd/system").is_dir() and bool(shutil.which("systemctl"))


def systemctl(*args, check=True, capture=True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", *args], capture_output=capture, text=True, check=check
    )


def query(*args) -> str:
    return systemctl(*args, check=False).stdout.strip()


def unit_states(names) -> dict:
    """name -> UnitState, for every name asked about, in one call.

    Not `--value`: it prints an empty line both for an empty property and
    between records, so a not-found unit cannot be told from a boundary.
    """
    names = list(names)
    if not names or not available():
        return {}
    result = subprocess.run(
        [
            "systemctl",
            "show",
            "-p",
            "Id,LoadState,ActiveState,UnitFileState",
            *names,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    states = {}
    record = {}
    for line in result.stdout.splitlines() + [""]:
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


def daemon_reload() -> None:
    if available():
        systemctl("daemon-reload", check=False)


@dataclass
class Plan:
    """Intended unit state, turned into the calls that would reach it.

    `calls()` is pure, so "a no-op deploy issues no systemctl calls" is a
    unit test rather than a stopwatch reading.
    """

    intents: dict = field(default_factory=dict)
    reset_failed: list = field(default_factory=list)

    def service(self, name, running=True, enabled=True, restart=False) -> None:
        self.intents[name] = (enabled, running, restart)

    def only(self, names) -> "Plan":
        """The part of this plan covering *names*, and nothing else."""
        chosen = Plan(reset_failed=[n for n in self.reset_failed if n in names])
        chosen.intents = {k: v for k, v in self.intents.items() if k in names}
        return chosen

    def without(self, names) -> "Plan":
        rest = Plan(reset_failed=[n for n in self.reset_failed if n not in names])
        rest.intents = {k: v for k, v in self.intents.items() if k not in names}
        return rest

    def units(self, names, running=True, enabled=True, restart=False) -> None:
        for name in names:
            self.service(
                name if "." in name else f"{name}.service",
                running=running,
                enabled=enabled,
                restart=restart,
            )

    def calls(self, states: dict) -> list:
        """The systemctl argv lists this plan needs, in the order to run them.

        Enable and disable batch into one call each, being pure symlink work.
        Starting does not: a failure has to name the unit and print its
        status, and the order units were declared in is a real dependency --
        opendkim will not start before unbound has written the root anchor.
        """
        enable, disable, start, restart, stop = [], [], [], [], []
        for name, (want_enabled, want_running, want_restart) in self.intents.items():
            state = states.get(name)
            if state is None or not state.exists:
                continue
            if want_enabled is True and not state.is_enabled:
                enable.append(name)
            elif want_enabled is False and state.enabled in (
                "enabled",
                "enabled-runtime",
            ):
                disable.append(name)
            if want_running is True:
                if want_restart and state.is_running:
                    restart.append(name)
                elif not state.is_running:
                    start.append(name)
            elif want_running is False and state.is_running:
                stop.append(name)

        argv = []
        for name in self.reset_failed:
            if states.get(name) and states[name].active == "failed":
                argv.append(["reset-failed", name])
        if enable:
            argv.append(["enable", "--no-reload", *sorted(enable)])
        if disable:
            argv.append(["disable", "--no-reload", *sorted(disable)])
        argv += [["stop", name] for name in stop]
        argv += [["start", name] for name in start]
        argv += [["restart", name] for name in restart]
        return argv


def run_calls(calls, out=None) -> list:
    """Issue the planned calls, and say which ones actually ran."""
    issued = []
    for call in calls:
        result = systemctl(*call, check=False)
        issued.append(call)
        if call[0] in ("start", "restart") and result.returncode:
            name = call[1]
            raise SystemdError(
                f"systemctl {call[0]} {name} failed:\n{result.stderr.strip()}\n"
                f"{query('status', '--no-pager', '--lines=20', name)}"
            )
        if result.returncode and out is not None:
            detail = result.stderr.strip().splitlines()
            if detail:
                out.detail(f"systemctl {' '.join(call)}: {detail[0]}")
    return issued


def stop_disable_mask(name: str, reload: bool = True) -> None:
    """Take a unit out of the way for good.

    Disable alone loses to the next apt or dnf upgrade re-enabling it.
    """
    flags = () if reload else ("--no-reload",)
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
        ["systemd-tmpfiles", "--create"], capture_output=True, text=True, check=False
    )
    return True


def unmask(name: str, reload: bool = True) -> None:
    systemctl("unmask", *(() if reload else ("--no-reload",)), name, check=False)


def enable(name: str, reload: bool = True) -> None:
    systemctl("enable", *(() if reload else ("--no-reload",)), name, check=False)


def read_units(state: Path) -> list:
    if not state.is_file():
        return []
    return [line.strip() for line in state.read_text().splitlines() if line.strip()]


def record_units(journal, state: Path, names) -> None:
    """Add to what a state file already lists, never replacing it."""
    merged = sorted(set(read_units(state)) | set(names))
    journal.write("cmrelay", state, "\n".join(merged) + "\n")


def unit_changed(journal) -> bool:
    """Whether anything systemd loads from disk moved this run."""
    return any(
        paths.SYSTEMD_SYSTEM in change.path.parents for change in journal.changes
    )
