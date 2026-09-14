"""The unit state a configuration intends, and the calls that reach it.

`install_daemon_units` writes the authored units during configuration;
`plan` turns intent into data against one systemd snapshot, so "a no-op
deploy issues no systemctl calls" stays a unit test. Around it sits what
`verbs.activate` runs: masking the resolvers competing with unbound,
deciding whether acmetool must reach the network, and `verify`, which
checks the deployed result instead of assuming it.
"""

from pathlib import Path

from . import paths, systemd
from .daemons import DKIM_SELECTOR, FILTERMAIL_UNITS
from .deploy import CHATMAILD_UNITS, TIMER_DRIVEN

# The five daemons Debian supplies units for and nixpkgs does not.
AUTHORED_UNITS = ["postfix", "dovecot", "nginx", "unbound", "opendkim"]

# Below this, deploy asks acmetool to reach the network; otherwise never.
CERT_GRACE_SECONDS = 604800

# Started before anything else, because acmetool answers the HTTP challenge
# and dovecot, postfix and nginx will not start without the certificate.
ACME_UNITS = (
    "acmetool-redirector.service",
    "acmetool-reconcile.service",
    "acmetool-reconcile.timer",
)


def install_daemon_units(d):
    for name in AUTHORED_UNITS:
        source = d.bundle.units / f"{name}.service"
        content = source.read_text().format(**daemon_unit_vars(d))
        d.j.install_unit(
            name, f"{name}.service", content, detail=component_label(d, name)
        )


def component_label(d, name: str) -> str:
    try:
        return f"{name} {d.bundle.version(name)}"
    except Exception:  # noqa: BLE001 - a label is not worth failing a deploy
        return ""


def daemon_unit_vars(d) -> dict:
    return {
        "postfix_bin": d.bin("postfix", "postfix"),
        "postfix_config_dir": str(paths.config_dir("postfix")),
        "dovecot_bin": d.bin("dovecot"),
        "doveadm_bin": d.bin("dovecot", "doveadm"),
        "dovecot_config": str(paths.config_dir("dovecot") / "dovecot.conf"),
        "nginx_bin": d.bin("nginx"),
        "nginx_prefix": d.bin("nginx", "prefix"),
        "nginx_config": str(paths.config_dir("nginx") / "nginx.conf"),
        "unbound_bin": d.bin("unbound"),
        "unbound_anchor_seed": str(paths.UNBOUND_ANCHOR_SEED),
        "unbound_config": str(paths.config_dir("unbound") / "unbound.conf"),
        "opendkim_bin": d.bin("opendkim"),
        "opendkim_config": str(paths.config_dir("opendkim") / "opendkim.conf"),
        "fcgiwrap_bin": d.bin("fcgiwrap"),
    }


def plan(d) -> systemd.Plan:
    """The unit state this configuration intends, as data."""
    plan = systemd.Plan()
    plan.reset_failed.append("unbound.service")
    plan.service("unbound.service", restart="unbound" in d.j)
    plan.units(["turnserver.service"], restart="turnserver" in d.j)

    iroh = bool(d.config.enable_iroh_relay)
    plan.service(
        "iroh-relay.service",
        running=iroh,
        enabled=iroh,
        restart="iroh" in d.j,
    )
    plan_tls(d, plan)
    plan_chatmaild(d, plan)
    plan.service("mta-sts-daemon.service", running=False, enabled=False)
    if not d.config.ipv4_relay:
        plan.service("opendkim.service", restart="opendkim" in d.j)
    plan.units(FILTERMAIL_UNITS, restart="filtermail" in d.j)

    active = not d.disable_mail
    for name in ("dovecot", "postfix"):
        plan.service(
            f"{name}.service",
            running=active,
            enabled=active,
            restart=name in d.j,
        )
    plan.service("fcgiwrap.socket", restart="fcgiwrap" in d.j)
    plan.service("fcgiwrap.service", restart="fcgiwrap" in d.j)
    plan.service("nginx.service", restart="nginx" in d.j)

    # No unit exists unless mtail was configured, so there is nothing to
    # disable on a host that never asked for one.
    if d.config.mtail_address:
        plan.service("mtail.service", restart="mtail" in d.j)
    plan.service("systemd-journald.service", enabled=None, restart="journald" in d.j)
    return plan


def plan_chatmaild(d, plan):
    for unit in CHATMAILD_UNITS:
        basename = unit if "." in unit else f"{unit}.service"
        # expire and fsreport are started by their timers, not directly
        enabled = unit not in TIMER_DRIVEN
        plan.service(
            basename,
            running=enabled,
            enabled=enabled,
            restart=enabled and "chatmaild" in d.j,
        )


def plan_tls(d, plan):
    mode = d.config.tls_cert_mode
    if mode == "acme":
        plan.service("acmetool-redirector.service")
        plan.service("acmetool-reconcile.service", running=False, enabled=False)
        plan.service("acmetool-reconcile.timer")
    elif mode == "external":
        plan.service("tls-cert-reload.path")


def mask_resolvers(states) -> list:
    """Mask the resolver managers competing with unbound for port 53.

    Only the ones not already masked, so a re-deploy spends nothing.
    """
    masked = []
    for unit, state in sorted(states.items()):
        if not state.exists or state.is_masked:
            continue
        systemd.stop_disable_mask(unit, reload=False)
        masked.append(unit)
    return masked


def needs_reconcile(d) -> bool:
    """Whether acmetool has to reach the network at all this run.

    The only network access on the deploy path; the reconcile timer owns
    renewals, so this runs only without a usable certificate.
    """
    cert = Path(d.config.tls_cert_path)
    if not cert.is_file():
        return True
    checkend = d.shell(
        d.bin("openssl"),
        "x509",
        "-checkend",
        str(CERT_GRACE_SECONDS),
        "-noout",
        "-in",
        str(cert),
        check=False,
    )
    if checkend.returncode:
        return True
    return not cert_covers(d, cert, d.tls_domains())


def cert_covers(d, cert: Path, domains) -> bool:
    result = d.shell(
        d.bin("openssl"),
        "x509",
        "-noout",
        "-ext",
        "subjectAltName",
        "-in",
        str(cert),
        check=False,
    )
    names = {
        entry.strip().removeprefix("DNS:")
        for line in result.stdout.splitlines()
        for entry in line.split(",")
        if entry.strip().startswith("DNS:")
    }
    return set(domains) <= names


def run_acmetool(d):
    d.out.detail(f"reconciling certificates for {', '.join(d.tls_domains())}")
    result = d.shell(
        d.bin("acmetool"),
        "--batch",
        "--xlog.severity=debug",
        f"--hooks={paths.ACME_HOOKS}",
        "reconcile",
        check=False,
    )
    if result.returncode:
        d.out.warn(
            "acmetool could not obtain a certificate. That needs DNS"
            " already pointing at this host and the challenge port"
            " reachable from the internet:\n" + result.stderr.strip()[-2000:]
        )


def verify(d) -> list:
    """Check that DKIM signing and TLS actually work, rather than assuming."""
    problems = []
    states = systemd.unit_states(
        ["opendkim.service", "dovecot.service", "postfix.service", "nginx.service"]
    )

    if not d.config.ipv4_relay:
        key = paths.DKIM_KEYS / f"{DKIM_SELECTOR}.private"
        if not key.is_file():
            problems.append(f"no DKIM key at {key}")
        elif (
            states.get("opendkim.service") and not states["opendkim.service"].is_running
        ):
            problems.append("opendkim.service is not running")

    for path in (d.config.tls_cert_path, d.config.tls_key_path):
        if not Path(path).is_file():
            problems.append(f"no TLS material at {path}")

    expected = ["dovecot.service", "postfix.service"] if not d.disable_mail else []
    for unit in [*expected, "nginx.service"]:
        state = states.get(unit)
        if state is not None and state.exists and not state.is_running:
            problems.append(f"{unit} is not running")
    return problems
