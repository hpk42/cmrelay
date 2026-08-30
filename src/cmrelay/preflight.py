"""Detect what already owns the ports and unit names cmrelay wants.

cmrelay ships its own builds under the distribution's service names, onto a
host where the distribution's packages may already be installed and
listening. Two detectors, because a name collision catches the package about
to fight us for port 25 while port occupancy catches the daemon cmrelay has
never heard of.

Nothing here modifies the host: only `deploy --take-over` acts on it.
"""

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, systemd

# Shadowed rather than masked: masking writes /etc/systemd/system/<name> ->
# /dev/null, the exact path cmrelay's own unit needs, so a masked name is one
# cmrelay cannot install.
SHADOWED_UNITS = [
    "postfix.service",
    "dovecot.service",
    "nginx.service",
    "unbound.service",
    "opendkim.service",
    "fcgiwrap.service",
]

# Not provided by cmrelay, so out of the way for good. postfix@-.service is
# the instance that binds port 25; leaving it gives a reboot two postfixes.
MASKED_UNITS = [
    "postfix@-.service",
    "exim4.service",
    "mta-sts-daemon.service",
]

CONFLICTING_UNITS = SHADOWED_UNITS + MASKED_UNITS

# Compete for /etc/resolv.conf with relay's unbound, and cannot be purged for
# dependency reasons, so they are masked.
RESOLVER_UNITS = [
    "systemd-resolved.service",
    # Fedora 43 splits resolved's IPC into sockets; masking only the service
    # leaves these to fail into `systemctl --failed`. Absent on Debian.
    "systemd-resolved-varlink.socket",
    "systemd-resolved-monitor.socket",
    "unbound-resolvconf.service",
]

# Named so the refusal says more than "port 25 is busy".
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

# `ss` truncates to 15 characters, hence the short forms.
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


class PreflightError(Exception):
    pass


class DetectorError(PreflightError):
    """A detector could not run, which is not the same as finding nothing."""


def _run(argv, required=True) -> str:
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


_SS_HOLDER = re.compile(r'\("([^"]+)"')


def listening(ss_argv=("ss",)) -> dict[int, set]:
    """port -> the process names listening on it, from one `ss` call.

    One listing costs 21 ms against 445 ms for twenty filtered ones. Store
    paths do not change argv[0] basenames, so relay's table matches.
    """
    holders: dict[int, set] = {}
    for line in _run([*ss_argv, "-lptnH"]).splitlines():
        fields = line.split(None, 5)
        if len(fields) < 5:
            continue
        _, _, port = fields[3].rpartition(":")
        try:
            number = int(port)
        except ValueError:
            continue
        users = fields[5] if len(fields) > 5 else ""
        names = set(_SS_HOLDER.findall(users))
        if names:
            holders.setdefault(number, set()).update(names)
    return holders


def resolve_port_table(entries, config) -> list[tuple[list[str], int]]:
    """Turn relay's extracted port table into concrete (names, port) pairs.

    Some entries reference a `config.<attr>` rather than a literal and one is
    conditional on acme mode, both resolved against relay's own Config.
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


def check_ports(port_table, ss_argv=("ss",), holders=None) -> Report:
    """Whoever actually holds each port, which catches daemons by no name.

    Needs no equivalent of check_units' *ours*: a port held by the process
    relay's table expects is right whoever installed it.
    """
    if holders is None:
        holders = listening(ss_argv)
    report = Report()
    for names, port in port_table:
        found = holders.get(port, set())
        if not found or found & set(names):
            continue
        # Taken over by configure_unbound(), and recorded for uninstall.
        if found & RESOLVER_PROCESSES:
            continue
        holder = sorted(found)[0]
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

    Without *ours* the detector cannot tell a distribution's postfix from the
    one cmrelay just installed, and would print a remedy that masks the name
    out of cmrelay's own reach.
    """
    ours = ours or (lambda name: False)
    report = Report()
    for name, state in systemd.unit_states(names or CONFLICTING_UNITS).items():
        if not state.exists or ours(name):
            continue
        enabled = state.enabled or "static"
        active = state.active or "unknown"
        if enabled in ("enabled", "enabled-runtime") or active == "active":
            report.add(
                name,
                f"already present and {enabled}/{active}",
                remedy=f"systemctl disable --now {name} && systemctl mask {name}",
            )
    return report


def check_mtail(address, artifact, binary=Path("/usr/local/bin/mtail")) -> Report:
    """mtail is the operator's to install, so say exactly how."""
    report = Report()
    if not address or os.access(binary, os.X_OK):
        return report
    url, sha256 = artifact
    report.add(
        "mtail",
        f"chatmail.ini sets mtail_address={address}, but {binary} is missing",
        remedy=(
            "install the release relay pins, or unset mtail_address:\n"
            f"        curl -fsSLo /tmp/mtail.tgz {url}\n"
            "        tar -xzOf /tmp/mtail.tgz mtail > /tmp/mtail\n"
            f'        echo "{sha256}  /tmp/mtail" | sha256sum -c\n'
            f"        install -m755 /tmp/mtail {binary}"
        ),
    )
    return report


def check_store(nix_root=Path("/nix")) -> Report:
    """A real /nix directory means the host runs Nix already.

    Uninstall removes the whole tree, so cmrelay will not mingle closures.
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


def check_external_tls(config) -> Report:
    """Both halves of an external certificate, before anything is written."""
    report = Report()
    if getattr(config, "tls_cert_mode", "") != "external":
        return report
    for path in (config.tls_cert_path, config.tls_key_path):
        if not Path(path).is_file():
            report.add(
                "external TLS",
                f"tls_external_cert_and_key names {path}, which is not a file",
                remedy=f"put the certificate and key at {path}, or change the ini",
            )
    return report


# The unpacked closure, checked before anything moves or is staged.
CLOSURE_MIB = 800


def check_free_space(needed_mib: int, path=Path("/opt")) -> Report:
    """Refuse with a number rather than filling the disk."""
    report = Report()
    target = path if path.exists() else Path("/")
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return report
    free_mib = usage.free // (1024 * 1024)
    if free_mib < needed_mib:
        report.add(
            "disk space",
            f"{target} has {free_mib} MiB free, and this needs {needed_mib} MiB",
            remedy=f"free {needed_mib - free_mib} MiB on {target}",
        )
    return report


def run(
    port_table,
    ss_argv=("ss",),
    nix_root=Path("/nix"),
    ours=None,
    config=None,
    needed_mib=0,
) -> Report:
    """The whole preflight, in three reads. Modifies nothing."""
    report = Report()
    parts = [
        check_store(nix_root),
        check_units(ours=ours),
        check_ports(port_table, ss_argv),
    ]
    if config is not None:
        parts.append(check_external_tls(config))
    if needed_mib:
        parts.append(check_free_space(needed_mib))
    for part in parts:
        report.findings.extend(part.findings)
    return report


def relay_pins(bundle) -> dict:
    return json.loads((bundle.root / "relay-pins.json").read_text())


def mtail_artifact(bundle) -> tuple:
    """The release relay pins for this architecture: (url, sha256)."""
    artifacts = relay_pins(bundle)["pins"]["MTAIL_ARTIFACTS"]
    machine = platform.machine()
    try:
        return tuple(artifacts[machine])
    except KeyError:
        raise PreflightError(
            f"relay pins no mtail release for {machine};"
            f" it has {', '.join(sorted(artifacts))}"
        ) from None


def run_preflight(bundle, config, needed_mib=0):
    """relay's port table resolved against this host's config, then one look."""
    resolved = resolve_port_table(relay_pins(bundle)["port_table"], config)
    report = run(
        resolved,
        ss_argv=bundle.command("iproute2", "ss"),
        ours=paths.owned_unit,
        config=config,
        needed_mib=needed_mib,
    )
    report.findings.extend(
        check_mtail(
            getattr(config, "mtail_address", ""), mtail_artifact(bundle)
        ).findings
    )
    return report


def format_report(report: Report) -> str:
    """The findings, with a remedy line under each that has one."""
    lines = []
    for finding in report.findings:
        lines.append(f"  {finding}")
        if finding.remedy:
            lines.append(f"      remedy: {finding.remedy}")
    return "\n".join(lines)
