"""Detect what already owns the ports and unit names cmrelay wants.

cmrelay faces a conflict class cmdeploy never does. cmdeploy installs
Debian's postfix, dovecot, nginx and unbound, so it *owns* those unit names
by construction. cmrelay ships its own builds under the same service names --
sameness is a contract, because the acceptance suite greps journald by unit
name -- onto a host where the distro packages may already be installed,
enabled and listening.

Two detectors, because names and ports catch different things. A unit name
collision catches the distro package that is about to fight us for port 25;
port occupancy catches the daemon cmrelay has never heard of.

Nothing here modifies the host. `install` reports and refuses; only
`install --take-over`
may act on what this finds.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Names cmrelay ships its own unit under, which a distribution may already
# own. These are *shadowed* on upgrade -- stopped and disabled, then the
# symlink replaced -- and never masked: masking writes
# /etc/systemd/system/<name> -> /dev/null, which is the exact path cmrelay's
# own symlink needs, so a masked name is a name cmrelay cannot install.
SHADOWED_UNITS = [
    "postfix.service",
    "dovecot.service",
    "nginx.service",
    "unbound.service",
    "opendkim.service",
    "fcgiwrap.service",
]

# Names cmrelay does not provide and must therefore put out of the way for
# good. Debian's postfix@-.service is the instance that actually binds port
# 25, so leaving it enabled would give a rebooted host two postfixes.
MASKED_UNITS = [
    "postfix@-.service",
    "exim4.service",
    "mta-sts-daemon.service",
]

CONFLICTING_UNITS = SHADOWED_UNITS + MASKED_UNITS

# Resolver managers that compete for /etc/resolv.conf with relay's unbound.
# cmdeploy stops and masks systemd-resolved because it cannot be purged for
# dependency reasons; cmrelay needs the same treatment.
RESOLVER_UNITS = [
    "systemd-resolved.service",
    # Fedora 43 splits resolved's IPC out into socket units. Masking only the
    # service leaves these to fail the moment anything touches them, which
    # puts a permanent entry in `systemctl --failed` -- noise that makes a
    # real fault harder to see. Absent on Debian, where unit_states skips
    # them.
    "systemd-resolved-varlink.socket",
    "systemd-resolved-monitor.socket",
    "unbound-resolvconf.service",
]

# The squatters a first-time installer actually meets, named so the refusal
# can say something more useful than "port 25 is busy".
KNOWN_SQUATTERS = {
    "exim4": "Debian's default MTA",
    "master": "an already-running postfix",
    "smtpd": "an already-running postfix",
    "sendmail": "sendmail",
    "apache2": "the Apache web server",
    "httpd": "the Apache web server",
    "named": "BIND",
    "systemd-resolve": "systemd-resolved",
    "systemd-r": "systemd-resolved",
}

# Process names belonging to the resolver units cmrelay takes over rather
# than refuses. `ss` truncates to 15 characters, hence the short forms.
RESOLVER_PROCESSES = {"systemd-resolve", "systemd-r", "systemd-resolved"}


@dataclass
class Finding:
    subject: str
    detail: str
    remedy: str | None = None

    def __str__(self):
        return f"{self.subject}: {self.detail}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, *args, **kwargs):
        self.findings.append(Finding(*args, **kwargs))


class DetectorError(Exception):
    """A detector could not run, which is not the same as finding nothing."""


def _run(argv, required=True) -> str:
    """Run a detector. Failing to start one is an error, not an empty result."""
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=30, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        if required:
            raise DetectorError(
                f"could not run {' '.join(str(a) for a in argv)}: {exc}."
                " Refusing to report a clean preflight from a detector that"
                " did not run."
            ) from exc
        return ""


def systemd_available() -> bool:
    return Path("/run/systemd/system").is_dir() and shutil.which("systemctl")


def template_of(name: str) -> str | None:
    """`postfix@-.service` -> `postfix@.service`, the name that has a file.

    An instantiated unit has no unit *file* of its own; only its template
    does. `systemctl list-unit-files` therefore never lists one, so a
    membership test against that listing answers "does not exist" for every
    `foo@instance.service` -- and a detector that skips what does not exist
    skips it in silence. Debian runs its postfix as `postfix@-.service`, so
    this is the difference between taking a host over and leaving its MTA
    running against the queue cmrelay is about to use.
    """
    stem, sep, rest = name.partition("@")
    if not sep or rest.startswith("."):
        return None
    suffix = rest.partition(".")[2]
    return f"{stem}@.{suffix}" if suffix else None


def unit_states(names) -> dict[str, tuple[str, str]]:
    """Return name -> (enabled-state, active-state) for units that exist."""
    if not systemd_available():
        return {}
    known = set()
    listing = _run(["systemctl", "list-unit-files", "--no-legend", "--no-pager"])
    for line in listing.splitlines():
        parts = line.split()
        if parts:
            known.add(parts[0])
    states = {}
    for name in names:
        if name not in known and template_of(name) not in known:
            continue
        # systemctl exits non-zero for disabled and inactive units, which is
        # an answer rather than a failure.
        enabled = _run(["systemctl", "is-enabled", name], required=False).strip()
        active = _run(["systemctl", "is-active", name], required=False).strip()
        states[name] = (enabled or "unknown", active or "unknown")
    return states


_SS_LINE = re.compile(r'users:\(\("([^"]+)"')


def port_holder(port: int, ss_argv=("ss",)) -> str | None:
    """Return the process name holding *port*, or None.

    Store paths do not change argv[0] basenames, so `master`, `smtpd`,
    `dovecot`, `imap-login`, `nginx`, `unbound`, `filtermail`, `iroh-relay`,
    `mtail` and `acmetool` all still match relay's table unchanged.
    """
    out = _run([*ss_argv, "-lptnH", f"sport = :{port}"])
    for line in out.splitlines():
        match = _SS_LINE.search(line)
        if match:
            return match.group(1)
    return None


def resolve_port_table(entries, config) -> list[tuple[list[str], int]]:
    """Turn relay's extracted port table into concrete (names, port) pairs.

    Six of the nineteen entries reference a `config.<attr>` rather than a
    literal, and one is conditional on acme mode; both are resolved here
    against the relay's own Config object.
    """
    resolved = []
    for entry in entries:
        if entry.get("conditional") and getattr(config, "tls_cert_mode", "") != "acme":
            continue
        if "port" in entry:
            port = entry["port"]
        else:
            attr = entry["config_attr"]
            try:
                port = int(getattr(config, attr))
            except (AttributeError, TypeError, ValueError):
                raise ValueError(
                    f"relay's port table references config.{attr},"
                    " which this chatmail.ini does not provide"
                ) from None
        resolved.append((entry["names"], port))
    return resolved


def check_ports(port_table, ss_argv=("ss",)) -> Report:
    """Whoever actually holds each port, which catches daemons by no name.

    Store paths do not change argv[0] basenames, so cmrelay's own postfix
    answers to `master` exactly as a distribution's does. That is why this
    needs no equivalent of check_units' *ours*: a port held by the process
    relay's table expects is right whoever installed it.
    """
    report = Report()
    for names, port in port_table:
        holder = port_holder(port, ss_argv=ss_argv)
        if holder in RESOLVER_PROCESSES:
            # Taken over by configure_unbound(), and recorded for uninstall.
            continue
        if holder and holder not in names:
            known = KNOWN_SQUATTERS.get(holder)
            detail = f"port {port} is held by {holder!r}"
            if known:
                detail += f" ({known})"
            detail += f", expected one of {', '.join(names)}"
            report.add(
                f"port {port}",
                detail,
                remedy=f"stop whatever runs {holder!r}, or free port {port}",
            )
    return report


def check_units(names=None, ours=None) -> Report:
    """Which of the names cmrelay wants are held by somebody else.

    *ours* answers "is this unit already cmrelay's own", and without it the
    detector cannot tell a distribution's postfix from the one cmrelay just
    installed. Reporting the latter would be worse than useless: the remedy
    it prints masks the name, and a masked name is one cmrelay can no longer
    install.
    """
    ours = ours or (lambda name: False)
    report = Report()
    for name, (enabled, active) in unit_states(names or CONFLICTING_UNITS).items():
        if ours(name):
            continue
        if enabled in ("enabled", "enabled-runtime") or active == "active":
            report.add(
                name,
                f"already present and {enabled}/{active}",
                remedy=f"systemctl disable --now {name} && systemctl mask {name}",
            )
    return report


def check_store(nix_root=Path("/nix")) -> Report:
    """A real /nix directory means the host runs Nix already.

    cmrelay stops rather than mingling closures with somebody else's store,
    because uninstall removes the whole tree.
    """
    report = Report()
    if nix_root.is_symlink():
        target = nix_root.resolve()
        expected = Path("/opt/cmrelay/nix")
        if target != expected:
            report.add(
                "/nix",
                f"is a symlink to {target}, not {expected}",
                remedy="remove or repoint /nix",
            )
    elif nix_root.exists():
        report.add(
            "/nix",
            "exists as a real directory: this host already runs Nix",
            remedy="cmrelay will not mingle closures; install on a host without Nix",
        )
    return report


def run(port_table, ss_argv=("ss",), nix_root=Path("/nix"), ours=None) -> Report:
    """The whole preflight. Modifies nothing."""
    report = Report()
    parts = (
        check_store(nix_root),
        check_units(ours=ours),
        check_ports(port_table, ss_argv),
    )
    for part in parts:
        report.findings.extend(part.findings)
    return report


def format_report(report: Report, remedies: bool = True) -> str:
    """The findings, and per-finding remedies unless something else fixes them.

    A caller about to say "re-run with --take-over" suppresses them: telling
    an operator to mask five services by hand, immediately above an offer to
    do exactly that, reads as two alternatives when only one is meant.
    """
    lines = []
    for finding in report.findings:
        lines.append(f"  {finding}")
        if remedies and finding.remedy:
            lines.append(f"      remedy: {finding.remedy}")
    return "\n".join(lines)
