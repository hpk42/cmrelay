"""`cmrelay status`: report, never modify."""

from .. import paths
from ..release import signature
from ..stack import config as cfg
from ..stack.deployment import Deployment
from ..stack.unbound import unbound
from ..system import fsops, prechecks, systemd


def status_verb(args, bundle, out) -> int:
    """0 healthy, 1 degraded, 2 not configured."""

    def row(key, value):
        out.print(f"{key:<16} {value}")

    row("cmrelay", bundle.manifest["version"])
    row("config", paths.INI)
    row("chatmail/relay", bundle.relay_commit)
    key = bundle.public_key
    if key:
        row("release key", signature.fingerprint(key.read_bytes()))
    row("closure", bundle.closure)
    if not paths.INI.is_file():
        out.warn(f"{paths.INI} does not exist; nothing is configured yet")
        out.print("  `cmrelay init DOMAIN` creates one")
        return 2

    config = cfg.read_config(paths.INI)
    row("mail domain", config.mail_domain)
    row("tls mode", config.tls_cert_mode)
    row("mailboxes", config.mailboxes_dir)

    wanted = Deployment(bundle, config, out, fsops.Changes()).units()
    names = paths.own_units()
    states = systemd.unit_states(names)
    bad = [n for n in names if wanted.get(n, (None,))[0] and not states[n].is_up]
    findings = prechecks.run_prechecks(bundle, config)
    # A failed host unit can be cmrelay's doing, like a wait-online without resolved.
    listed = systemd.query("list-units", "--failed", "--plain", "--no-legend")
    failed = [line.split()[0] for line in listed.splitlines() if line.strip()]
    failed = [name for name in failed if name not in names]
    # resolv.conf names unbound first, so with it silent every lookup waits out a timeout
    query = ("drill", "-Q", "@127.0.0.1", "localhost")
    silent = unbound.first_nameserver() == "127.0.0.1" and bool(
        bundle.run(*query, check=False).returncode
    )

    row("packages", len(bundle.packages))
    verdict = f"{len(bad)} not as intended" if bad else "all good"
    row("units", f"{len(names)}, {verdict}" if names else "none installed yet")
    if findings:
        row("conflicts", len(findings))
    if bad or findings or failed or silent:
        counts = [(len(bad), "unit"), (len(findings), "conflict")]
        counts += [(len(failed), "failed host unit"), (int(silent), "silent resolver")]
        what = ", ".join(f"{n} {word}{'s' * (n != 1)}" for n, word in counts if n)
        row("overall status", f"not green: {what}; see below")
    else:
        out.ok(f"{'overall status':<16} all green, congrats!")

    if out.verbose:
        out.section("Packages")
        for name in sorted(bundle.packages):
            out.print(f"  {name:<16} {bundle.packages[name]['version']}")
    if out.verbose or bad:
        out.section("Units")
    for name in names if out.verbose else bad:
        unit = states[name]
        line = f"  {name:<36} {unit.enabled or '-':<10} {unit.active or '-'}"
        (out.error if name in bad else out.print)(line)
    if failed:
        out.section("Failed host units")
        for name in failed:
            out.error(f"  {name:<36} see systemctl status {name}")
    if silent:
        out.section("Local resolver")
        out.error(
            f"  unbound at 127.0.0.1 does not answer, and {unbound.RESOLV_CONF} names it first"
        )
    if findings:
        out.section("Conflicts")
        out.print(prechecks.format_report(findings))
    if not out.verbose:
        out.print("\nuse -v for more info")
    return 1 if bad or findings or failed or silent else 0
