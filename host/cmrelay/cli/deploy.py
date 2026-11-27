"""`cmrelay deploy`."""

import sys
import textwrap
import time
from pathlib import Path

from .. import paths
from ..release import store
from ..stack import config as cfg
from ..stack import users
from ..stack.deployment import Deployment
from ..stack.tls import tls
from ..stack.unbound import unbound
from ..system import fsops, prechecks, systemd
from ..system.host import Host
from .dns import dns

# a unit name holds hyphens, and breaks only after its comma
WRAP = textwrap.TextWrapper(76, subsequent_indent="    ", break_on_hyphens=False)


def deploy_verb(args, bundle, out) -> int:
    started = time.monotonic()
    changes = fsops.Changes(marker=f"{paths.MARKER} {bundle.manifest['version']}")

    where = Host.classify()
    config = cfg.read_config(paths.INI)
    domain = config.mail_domain
    disable_mail = args.disable_mail
    checks_dns = not (args.skip_dns_check or config.ipv4_relay)

    mode, commit = config.tls_cert_mode, bundle.relay_commit[:10]
    out.section("Relay")
    out.print(f"  Host        {where.description}")
    out.print(f"  Domain      {domain}  ({mode}, {config.mailboxes_dir})")
    out.print(
        f"  Version     cmrelay {bundle.manifest['version']}, chatmail/relay {commit}"
    )

    out.section("Prechecks")
    findings = prechecks.run_prechecks(bundle, config)
    refusal = check_prechecks(where, findings, out)
    if refusal is not None:
        return refusal
    for warning in cfg.warnings(config, paths.INI):
        out.warn(warning)
    if checks_dns and mode == "acme" and not check_dns_ready(bundle, config, args, out):
        return 1

    if where.takes_over and not take_over(where, findings, bundle, out, args, changes):
        return 1

    deployment = Deployment(bundle, config, out, changes, disable_mail)

    out.section("Configuration")
    # an aborted deploy may have written files whose services never restarted
    changes.redo = paths.DEPLOY_PENDING.exists()
    paths.DEPLOY_PENDING.touch()
    users.ensure(changes)
    digest = fsops.file_digest(paths.INI)
    changes.write("chatmail.ini", paths.INI_DIGEST, f"{digest}\n")
    deployment.configure()
    report_changes(out, changes)

    out.section("Services")
    moved = activate(deployment, changes, out)

    out.section("Verify")
    problems = deployment.verify()
    for problem in problems:
        out.error(f"  {problem}")
    if problems:
        out.error(f"{domain} is not fully deployed: see the {len(problems)} above")
        return 1
    paths.DEPLOY_PENDING.unlink()
    dkim = "" if config.ipv4_relay else "DKIM key present, "
    out.detail(f"{dkim}TLS material present, services running")
    store.prune(out)
    summarise(out, changes, moved, domain, time.monotonic() - started, disable_mail)
    if not config.ipv4_relay and (changes or moved):
        out.print("\nNext: `cmrelay dns` lists the DNS records still missing.")
    return 0


def check_dns_ready(bundle, config, args, out) -> bool:
    """The records ACME needs, checked before anything is written.

    acmetool fails only after the deploy, and its errors hardly name the wrong record.
    """
    # After an interrupted deploy resolv.conf may name an unbound that is not running.
    if unbound.first_nameserver() == "127.0.0.1":
        resolver = systemd.unit_states(["unbound.service"]).get("unbound.service")
        if resolver is None or not resolver.is_running:
            out.detail("the local resolver is not running; skipping the DNS check")
            return True
    domain = config.mail_domain
    while True:
        answers = dns.acme_answers(bundle, domain)
        problems, records = dns.acme_readiness(domain, answers, dns.this_host())
        if not problems:
            return True
        out.error(f"acmetool cannot get a certificate for {domain} yet:")
        for problem in problems:
            out.print(f"  {problem}")
        if records:
            out.print(f"\n  Publish at {domain}'s DNS provider:")
            for record in records:
                out.print(f"    {record}")
        out.print("")
        if args.yes or not sys.stdin.isatty() or not out.confirm("Check again?"):
            out.print("Run `cmrelay deploy` once they are, or add --skip-dns-check.")
            return False


def check_prechecks(where, findings, out):
    """Whether the deploy may proceed, and what to say if it may not."""
    postscript = ""
    conflict = any(kind == prechecks.CONFLICT for kind, *_ in findings)
    if where.state == Host.BARE and conflict:
        error = "this host runs a mail stack cmrelay did not install"
        postscript = (
            "If this host is meant to keep the stack above, cmrelay is not"
            "\nfor it. There is no chatmail deployment here to take over."
        )
    elif any(kind == prechecks.MISSING for kind, *_ in findings):
        error = "this host lacks something its configuration needs"
    elif not findings or where.takes_over:
        return None
    else:
        error = "another unit holds a name this host needs"
    out.error(error)
    out.print(prechecks.format_report(findings))
    out.print("\nNo configuration and no service on this host was modified.")
    if postscript:
        out.print(postscript)
    return 1


def take_over(where, findings, bundle, out, args, changes) -> bool:
    """Stop, disable and mask the stack that is already here."""
    if where.relay_commit:
        out.detail(f"{paths.VERSION_MARKER}: chatmail/relay {where.relay_commit[:12]}")
        if where.relay_commit != bundle.relay_commit:
            out.detail(f"this cmrelay carries {bundle.relay_commit[:12]}")

    conflicts = [f for f in findings if f[0] == prechecks.CONFLICT]
    if conflicts:
        out.print("\nUnits a take-over stops and disables:")
        for _, name, detail, _ in conflicts:
            out.print(f"  {name} ({detail})")
    if not out.confirm("\nConvert this host to cmrelay?", args.yes):
        return False

    out.section("Take-over")
    retire(prechecks.SHADOWED_UNITS, changes, out, mask=False)
    retire(prechecks.MASKED_UNITS, changes, out, mask=True)
    retire(prechecks.CMDEPLOY_STALEUNITS, changes, out, mask=False)
    systemd.daemon_reload()
    return True


def retire(names, changes, out, mask) -> list:
    """Stop, disable and maybe mask these units, recorded for uninstall to undo."""
    retired, undo = [], []
    for name, unit in systemd.unit_states(names).items():
        if not unit.exists or unit.is_masked or paths.owned_unit(name):
            continue
        how = "masked" if systemd.retire(name, mask=mask) else "disabled"
        out.changed(f"{name} ({unit.enabled}/{unit.active}) stopped and {how}")
        retired.append(name)
        if how == "masked":
            undo.append(f"unmask {name}")
        if unit.enabled == "enabled":
            undo.append(f"enable {name}")
    if undo:
        changes.add_lines(paths.UNDO, undo)
    return retired


def activate(deployment, changes, out) -> list:
    """One reload if unit files moved, then one systemctl call per verb."""
    # Every deploy, not only a take-over: a bare host runs systemd-resolved too.
    retired = retire(unbound.RESOLVER_UNITS, changes, out, mask=True)
    units = deployment.units()
    for name in (n for n in paths.own_units() if n not in units):
        systemd.retire(name)
        changes.remove_file("cmrelay", paths.SYSTEMD_SYSTEM / name)
        systemd.systemctl("reset-failed", name)
        out.changed(f"{name} stopped and removed: this release has no such unit")
    in_systemd = (paths.SYSTEMD_SYSTEM in c.path.parents for c in changes)
    if any(in_systemd) or retired:
        systemd.daemon_reload()

    states = systemd.unit_states(units)
    resolver = states.get("unbound.service")
    if resolver and resolver.active == "failed":
        systemd.systemctl("reset-failed", "unbound.service")

    waiting = {name: units.pop(name) for name in Deployment.TLS_UNITS}
    moved = systemd.apply(units, states)
    config = deployment.config
    if config.tls_cert_mode == "acme" and tls.needs_reconcile(deployment):
        tls.run_acmetool(deployment)
    certified = Path(config.tls_cert_path).is_file()
    if certified:
        moved += systemd.apply(waiting, states)
    for verb in ("enable", "disable", "stop", "start", "restart"):
        names = [name for name, done in moved if done == verb]
        if names:
            out.changed(WRAP.fill(f"{verb}: {', '.join(names)}"))
    if certified and not moved:
        out.unchanged(f"{len(states)} units, all as intended")
    if any(verb in ("enable", "disable") for _, verb in moved):
        systemd.daemon_reload()
    return moved


def report_changes(out, changes):
    """One count per component, or every path with --verbose."""
    by_component = {}
    for change in changes:
        by_component.setdefault(change.component, []).append(change)
    for name in sorted(by_component):
        changes = by_component[name]
        if out.verbose:
            for change in changes:
                out.changed(str(change.path))
        else:
            out.changed(f"{name:<18}{len(changes)} files")
    if not by_component:
        out.unchanged("nothing changed")


def summarise(out, changes, moved, domain, seconds, disable_mail):
    started = sum(1 for _, verb in moved if verb in ("start", "restart"))
    if not changes and not moved:
        out.ok(f"\nnothing to do; {domain} is up to date ({seconds:.2f}s)")
        return
    parts = [f"{len(changes)} files written"]
    if started:
        parts.append(f"{started} services started")
    out.ok(f"\ndeployed {domain} in {seconds:.1f}s: {', '.join(parts)}")
    if disable_mail:
        out.print(
            "postfix and dovecot stay stopped until a deploy without --disable-mail"
        )
