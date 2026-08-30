"""deploy, status, uninstall and version.

`deploy` is the workhorse and the only verb that writes. It classifies the
host, resolves the domain, and then runs five phases: preflight, render,
reconcile, activate, verify. Take-over differs only by a preamble.
"""

import os
import time
from pathlib import Path
from types import SimpleNamespace

from . import activation, fsops, host, paths, preflight, state, systemd, users
from . import config as cfg
from .bundle import Bundle
from .deploy import Deployment
from .dnscheck import dns_verb, resolves
from .installer import installer_verb
from .status import status_verb, version_verb
from .uninstall import uninstall_verb
from .upgrade import upgrade_verb
from .version import __version__

# Shown individually before the report collapses the rest into a count.
SYSTEM_LINES = 6


class VerbError(Exception):
    pass


def require_root():
    if os.geteuid() != 0:
        raise VerbError("cmrelay must run as root")


# -- deploy ---------------------------------------------------------------


def cmd_deploy(args, out) -> int:
    require_root()
    bundle = Bundle.discover()
    with fsops.lock():
        return deploy(args, bundle, out)


def deploy(args, bundle, out) -> int:
    started = time.monotonic()
    journal = fsops.Journal(dry_run=args.dry_run, prior=state.read_written())

    where = host.classify()
    existing = load_existing_config(where)
    domain = host.resolve_domain(
        where,
        args.domain,
        force=args.force,
        mailboxes_dir=getattr(existing, "mailboxes_dir", None),
    )
    config = existing or planned_config(args, domain)

    intent = state.read_intent()
    disable_mail = not state.resolve_mail_intent(intent, args.enable_mail)

    out.header(
        "Relay",
        [
            ("Host", where.description),
            (
                "Domain",
                f"{domain}  ({config.tls_cert_mode}, {config.mailboxes_dir})",
            ),
            ("Version", f"cmrelay {__version__}, relay {bundle.relay_commit[:10]}"),
        ],
    )

    with out.step("Preflight"):
        needed = 0 if where.state == host.INSTALLED else preflight.CLOSURE_MIB
        report = preflight.run_preflight(bundle, config, needed_mib=needed)
        where = host.escalate(where, report)
        refusal = check_preflight(where, report, out)
        if refusal is not None:
            return refusal
        for warning in cfg.warnings(config, where.ini or paths.INI):
            out.warn(warning)
        if not check_domain_resolves(bundle, config, args, out):
            return 1
        if not report.ok:
            out.detail("take-over will stop and mask what is in the way")
        else:
            out.detail("no conflicting units, no occupied ports, no existing store")

    if where.takes_over and not take_over(where, bundle, out, args, journal):
        return 1

    deployment = Deployment(
        bundle,
        config,
        out,
        journal,
        disable_mail=disable_mail,
        reconcile=args.reconcile,
    )

    with out.step("Configuration"):
        adopt_or_write_config(where, args, domain, journal, out)
        created = users.ensure_required_users()
        if created:
            out.detail(f"created {', '.join(created)}")
        deployment.config = cfg.read_config()
        if args.website_only:
            deployment.configure_website()
        else:
            deployment.configure()
            activation.install_daemon_units(deployment)
        state.save_intent(journal, {**intent, "disable_mail": disable_mail})
        state.save_written(journal)
        report_components(out, journal)

    report_system_files(out, journal)

    issued = []
    if not (args.website_only or args.dry_run):
        with out.step("Services"):
            issued = activate(deployment, journal, out)

    problems = []
    if not (args.website_only or args.dry_run):
        with out.step("Verify"):
            problems = activation.verify(deployment)
            for problem in problems:
                out.error(f"  {problem}")
            if not problems:
                out.detail("DKIM key present, TLS material present, services running")

    report_hand_edits(out, journal)
    summarise(out, journal, issued, domain, time.monotonic() - started, disable_mail)
    if wants_dns_report(args, journal, issued, problems):
        report_dns(bundle, deployment.config, out)
    return 1 if problems else 0


def wants_dns_report(args, journal, issued, problems) -> bool:
    """Only when something moved: a no-op deploy reaches the network never."""
    if args.website_only or args.dry_run or args.skip_dns_check or problems:
        return False
    return bool(journal.changes or issued)


def report_dns(bundle, config, out) -> None:
    """The records still to publish. Never fails the deploy: the DKIM record
    they need did not exist until a moment ago."""
    if config.ipv4_relay:
        return
    out.next_step("publish DNS records. `cmrelay dns` lists what is missing.")
    try:
        dns_verb(SimpleNamespace(zonefile=None), bundle, out)
    except Exception as exc:  # noqa: BLE001 - reporting, not gating
        out.detail(f"could not check DNS: {exc}")


def load_existing_config(where):
    """The config already on this host, from cmrelay's ini or cmdeploy's."""
    if where.ini is None:
        return None
    return cfg.read_config(where.ini)


def planned_config(args, domain):
    """What the ini this run is about to write would say."""
    if args.config:
        source = Path(args.config)
        if not source.is_file():
            raise VerbError(f"--config {source} does not exist")
        return cfg.read_config(source)
    return cfg.default_config(domain, cfg.parse_overrides(args.set))


def check_domain_resolves(bundle, config, args, out) -> bool:
    """One lookup, before 800 MiB of store moves for a typo'd domain.

    ACME cannot possibly succeed without an A or AAAA record, and a
    half-deployed relay with no certificate is the worst outcome available.
    """
    if args.skip_dns_check or config.ipv4_relay:
        return True
    try:
        if resolves(bundle, config.mail_domain):
            return True
    except Exception as exc:  # noqa: BLE001 - a resolver failure is not a refusal
        out.detail(f"could not resolve {config.mail_domain}: {exc}")
        return True
    if config.tls_cert_mode == "acme":
        out.error(
            f"{config.mail_domain} has no A or AAAA record, so acmetool cannot"
            " obtain a certificate and this deploy would leave the relay"
            " without one."
            "\n  Publish the record first, or add --skip-dns-check."
        )
        return False
    out.warn(f"{config.mail_domain} has no A or AAAA record yet")
    return True


def check_preflight(where, report, out):
    """Whether the deploy may proceed, and what to say if it may not."""
    postscript = ""
    if where.state == host.FOREIGN:
        error = "this host runs a mail stack cmrelay did not install"
        postscript = (
            "If this host is meant to keep the stack above, cmrelay is not"
            "\nfor it. There is no chatmail deployment here to take over."
        )
    elif any(f.subject == "mtail" for f in report.findings):
        error = "this configuration needs mtail, which cmrelay does not ship"
    elif report.ok or where.takes_over:
        return None
    else:
        error = "something is in the way and this host carries no chatmail markers"
    out.error(error)
    out.print(preflight.format_report(report))
    out.print("\nNo configuration and no service on this host was modified.")
    if postscript:
        out.print(postscript)
    return 1


def take_over(where, bundle, out, args, journal) -> bool:
    """Stop, disable and mask the stack that is already here."""
    if where.relay_commit:
        out.detail(f"{paths.VERSION_MARKER}: relay {where.relay_commit[:12]}")
        if where.relay_commit != bundle.relay_commit:
            out.detail(f"cmrelay vendored relay {bundle.relay_commit[:12]}")
    if where.hand_modified:
        out.warn(
            "this host was deployed from a modified working tree;"
            " its configuration may not match any relay commit"
        )
    if where.ini:
        mtail = preflight.check_mtail(
            getattr(cfg.read_config(where.ini), "mtail_address", ""),
            preflight.mtail_artifact(bundle),
        )
        if not mtail.ok:
            out.error(
                "this host's configuration needs mtail, which cmrelay does not ship"
            )
            out.print(preflight.format_report(mtail))
            return False

    conflicts = preflight.check_units(ours=paths.owned_unit)
    if not conflicts.ok:
        out.print("\nUnits cmrelay will stop, disable and mask:")
        for finding in conflicts.findings:
            out.print(f"  {finding.subject} ({finding.detail})")

    if not (args.take_over or args.yes):
        out.print("")
        if not out.confirm("Convert this host to cmrelay?"):
            out.print("aborted; nothing was modified")
            return False

    with out.step("Take-over"):
        masked, shadowed = mask_existing(out)
        if shadowed:
            state.record_units(journal, paths.SHADOWED_UNITS_STATE, shadowed)
        if masked:
            state.record_units(journal, paths.DISABLED_UNITS, masked)
        systemd.daemon_reload()
    return True


def mask_existing(out):
    """Shadowed names cmrelay reuses, masked names it does not provide."""
    states = systemd.unit_states(
        preflight.SHADOWED_UNITS + preflight.MASKED_UNITS + preflight.RESOLVER_UNITS
    )
    masked, shadowed = [], []
    for name in preflight.SHADOWED_UNITS:
        unit = states.get(name)
        if paths.owned_unit(name) or unit is None or not unit.exists:
            continue
        out.changed(f"{name} ({unit.enabled}/{unit.active}) stopped and disabled")
        if unit.enabled in ("enabled", "enabled-runtime"):
            shadowed.append(name)
        systemd.systemctl("stop", name, check=False)
        systemd.systemctl("disable", "--no-reload", name, check=False)
        systemd.unmask(name, reload=False)

    for name in preflight.MASKED_UNITS + preflight.RESOLVER_UNITS:
        unit = states.get(name)
        if unit is None or not unit.exists or unit.is_masked:
            continue
        out.changed(f"{name} ({unit.enabled}/{unit.active}) stopped and masked")
        systemd.stop_disable_mask(name, reload=False)
        masked.append(name)
    return masked, shadowed


def adopt_or_write_config(where, args, domain, journal, out):
    """chatmail.ini is written or adopted exactly once, then left alone."""
    if paths.INI.is_file() and not (args.set or args.config):
        return
    if args.config:
        source = Path(args.config)
        journal.copy("cmrelay", source, paths.INI)
        out.detail(f"copied {source} -> {paths.INI}")
        return
    if where.ini and where.ini != paths.INI:
        journal.copy("cmrelay", where.ini, paths.INI)
        out.detail(f"adopted {where.ini} -> {paths.INI}")
        return
    if not paths.INI.is_file():
        journal.ensure_directory("cmrelay", paths.INI.parent)
        cfg.write_initial_config(paths.INI, domain, cfg.parse_overrides(args.set))
        journal.produced("cmrelay", paths.INI, "generated from relay's defaults")
        out.added(f"{paths.INI}")


def activate(deployment, journal, out) -> list:
    """One reload if unit files moved, then only the calls that differ."""
    if not systemd.available():
        out.warn("no systemd here; leaving every service alone")
        return []

    masked = activation.mask_resolvers(systemd.unit_states(preflight.RESOLVER_UNITS))
    if masked:
        state.record_units(journal, paths.DISABLED_UNITS, masked)

    reloaded = False
    if systemd.unit_changed(journal) or masked:
        systemd.daemon_reload()
        reloaded = True

    plan = activation.plan(deployment)
    states = systemd.unit_states(sorted(plan.intents))

    # acmetool answers the HTTP challenge, and dovecot, postfix and nginx
    # will not start without the certificate it fetches.
    issued = issue_calls(out, plan.only(activation.ACME_UNITS).calls(states))
    if deployment.config.tls_cert_mode == "acme" and activation.needs_reconcile(
        deployment
    ):
        activation.run_acmetool(deployment)
    if not tls_material_present(deployment, out):
        return issued

    issued += issue_calls(out, plan.without(activation.ACME_UNITS).calls(states))
    if not issued:
        out.unchanged(f"{len(plan.intents)} units, all as intended")
    if any(call[0] in ("enable", "disable") for call in issued) and not reloaded:
        systemd.daemon_reload()
    return issued


def issue_calls(out, calls) -> list:
    issued = systemd.run_calls(calls, out=out)
    for call in issued:
        names = [a for a in call[1:] if not a.startswith("--")]
        out.changed(f"{', '.join(names)}: {call[0]}")
    return issued


def tls_material_present(deployment, out) -> bool:
    """Refuse to start the mail services rather than dump a systemd failure."""
    cert = Path(deployment.config.tls_cert_path)
    if cert.is_file():
        return True
    out.error(
        f"there is no TLS certificate at {cert}, so dovecot, postfix and"
        " nginx cannot start."
    )
    if deployment.config.tls_cert_mode == "acme":
        out.print(
            "  acmetool needs this host's A or AAAA record published and"
            + "\n  port 80 reachable from the internet. Publish the record and"
            + "\n  run `cmrelay deploy` again, or use tls_cert_mode = self for"
            + "\n  a test host."
        )
    return False


# -- reporting -------------------------------------------------------------


def report_components(out, journal):
    """cmrelay's own configuration, collapsed into a count per component."""
    own = journal.own_changes
    by_component = {}
    for change in own:
        by_component.setdefault(change.component, []).append(change)
    for name in sorted(by_component):
        changes = by_component[name]
        if out.verbose:
            for change in changes:
                out.mark(change.kind, str(change).removeprefix(f"{change.kind} "))
        else:
            out.changed(f"{name:<18}{len(changes)} files")
    quiet = journal.total_seen - len(own)
    if quiet > 0:
        out.unchanged(f"{quiet} files unchanged")


def report_system_files(out, journal):
    """Every file outside /opt/cmrelay that moved, named."""
    changes = journal.system_changes
    out.section_header("System files", journal.system_seconds)
    if not changes:
        out.unchanged("nothing changed")
        return
    shown = changes if out.verbose else changes[:SYSTEM_LINES]
    for change in shown:
        out.mark(change.kind, str(change).removeprefix(f"{change.kind} "))
    hidden = len(changes) - len(shown)
    if hidden > 0:
        out.detail(f"... {hidden} more, all named with --verbose")


def report_hand_edits(out, journal):
    for path, action in journal.hand_edited:
        out.warn(f"{path} was edited by hand: {action}")


def summarise(out, journal, issued, domain, seconds, disable_mail):
    started = sum(1 for call in issued if call[0] in ("start", "restart"))
    if not journal.changes and not issued:
        out.summary(f"nothing to do; {domain} is up to date ({seconds:.2f}s)")
        return
    parts = [f"{len(journal.changes)} files written"]
    if started:
        parts.append(f"{started} services started")
    out.summary(f"deployed {domain} in {seconds:.1f}s: {', '.join(parts)}")
    if disable_mail:
        out.print("postfix and dovecot stay stopped: --disable-mail is in force")


# -- dispatch --------------------------------------------------------------


def cmd_dns(args, out) -> int:
    return dns_verb(args, Bundle.discover(), out)


def cmd_upgrade(args, out) -> int:
    require_root()
    with fsops.lock():
        return upgrade_verb(args, Bundle.discover(), out)


def cmd_installer(args, out) -> int:
    return installer_verb(args, out)


def cmd_uninstall(args, out) -> int:
    require_root()
    with fsops.lock():
        return uninstall_verb(args, out)


VERBS = {
    "deploy": cmd_deploy,
    "dns": cmd_dns,
    "installer": cmd_installer,
    "status": status_verb,
    "uninstall": cmd_uninstall,
    "upgrade": cmd_upgrade,
    "version": version_verb,
}


def dispatch(args, out) -> int:
    return VERBS[args.verb](args, out)
