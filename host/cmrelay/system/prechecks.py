"""What stands in a deploy's way, found without modifying the host."""

import os
import platform
from pathlib import Path

from .. import paths
from . import systemd

# Shadowed, not masked: a mask occupies the path cmrelay's own unit needs.
SHADOWED_UNITS = [
    "postfix.service",
    "dovecot.service",
    "nginx.service",
    "unbound.service",
    "opendkim.service",
    "fcgiwrap.service",
]

# Not provided by cmrelay, so masked. postfix@-.service binds port 25.
MASKED_UNITS = [
    "postfix@-.service",
    "exim4.service",
    "mta-sts-daemon.service",
]

# A finding is (kind, subject, detail, remedy).
CONFLICT = "conflict"
MISSING = "missing"


def check_units() -> list:
    """Which of the names cmrelay wants are held by somebody else's unit."""
    findings = []
    for name, state in systemd.unit_states(SHADOWED_UNITS + MASKED_UNITS).items():
        if not state.exists or paths.owned_unit(name):
            continue
        enabled = state.enabled or "static"
        active = state.active or "unknown"
        if enabled in ("enabled", "enabled-runtime") or active == "active":
            detail = f"already present and {enabled}/{active}"
            remedy = f"systemctl disable --now {name}"
            if name not in SHADOWED_UNITS:
                remedy += f" && systemctl mask {name}"
            findings.append((CONFLICT, name, detail, remedy))
    return findings


def check_mtail(bundle, config) -> list:
    """mtail is the operator's to install, so say exactly how."""
    binary = paths.MTAIL
    if not config.mtail_address or os.access(binary, os.X_OK):
        return []
    url, sha256 = bundle.pins["MTAIL_ARTIFACTS"][platform.machine()]
    detail = f"chatmail.ini sets mtail_address={config.mtail_address}, but {binary} is missing"
    remedy = (
        "install it, or unset mtail_address:\n"
        f"        curl -fsSLo /tmp/mtail.tgz {url}\n"
        "        tar -xzOf /tmp/mtail.tgz mtail > /tmp/mtail\n"
        f'        echo "{sha256}  /tmp/mtail" | sha256sum -c\n'
        f"        install -m755 /tmp/mtail {binary}"
    )
    return [(MISSING, "mtail", detail, remedy)]


def check_external_tls(config) -> list:
    """Both halves of an external certificate, before anything is written."""
    if config.tls_cert_mode != "external":
        return []
    return [
        (
            MISSING,
            "external TLS",
            f"tls_external_cert_and_key names {path}, which is not a file",
            f"put the certificate and key at {path}, or change the ini",
        )
        for path in (config.tls_cert_path, config.tls_key_path)
        if not Path(path).is_file()
    ]


def run_prechecks(bundle, config) -> list:
    """All findings for this host and config. Modifies nothing."""
    return check_units() + check_external_tls(config) + check_mtail(bundle, config)


def format_report(findings) -> str:
    """The findings, each with its remedy beneath."""
    return "\n".join(
        f"  {subject}: {detail}\n      remedy: {remedy}"
        for _, subject, detail, remedy in findings
    )
