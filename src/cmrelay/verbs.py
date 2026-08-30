"""deploy, status, uninstall and version.

`deploy` is the workhorse and the only verb that writes. It classifies the
host, resolves the domain, and then runs five phases: preflight, render,
reconcile, activate, verify. Take-over differs only by a preamble.
"""

import json
import os
import platform
import time
from pathlib import Path
from types import SimpleNamespace

from . import activation, fsops, host, paths, preflight, state, store, systemd, users
from . import config as cfg
from .bundle import Bundle
from .deploy import TIMER_DRIVEN, Deployment
from .dnscheck import dns_verb, resolves
from .installer import installer_verb
from .signing import trusted_keys
from .upgrade import upgrade_verb
from .version import __version__

# Shown individually before the report collapses the rest into a count.
SYSTEM_LINES = 6


class VerbError(Exception):
    pass


def require_root():
    if os.geteuid() != 0:
        raise VerbError("cmrelay must run as root")


def relay_pins(bundle) -> dict:
    return json.loads((bundle.root / "relay-pins.json").read_text())


def mtail_artifact(bundle) -> tuple:
    """The release relay pins for this architecture: (url, sha256)."""
    artifacts = relay_pins(bundle)["pins"]["MTAIL_ARTIFACTS"]
    machine = platform.machine()
    try:
        return tuple(artifacts[machine])
    except KeyError:
        raise VerbError(
            f"relay pins no mtail release for {machine};"
            f" it has {', '.join(sorted(artifacts))}"
        ) from None


def run_preflight(bundle, config, needed_mib=0):
    """relay's port table resolved against this host's config, then one look."""
    resolved = preflight.resolve_port_table(relay_pins(bundle)["port_table"], config)
    report = preflight.run(
        resolved,
        ss_argv=bundle.command("iproute2", "ss"),
        ours=owned_unit,
        config=config,
        needed_mib=needed_mib,
    )
    report.findings.extend(
        preflight.check_mtail(
            getattr(config, "mtail_address", ""), mtail_artifact(bundle)
        ).findings
    )
    return report


def owned_unit(name: str) -> bool:
    """Whether this unit name is already served by a cmrelay unit file."""
    return (paths.UNITS / name).is_symlink()


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
        report = run_preflight(bundle, config, needed_mib=needed)
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
            mtail_artifact(bundle),
        )
        if not mtail.ok:
            out.error(
                "this host's configuration needs mtail, which cmrelay does not ship"
            )
            out.print(preflight.format_report(mtail))
            return False

    conflicts = preflight.check_units(ours=owned_unit)
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
        if owned_unit(name) or unit is None or not unit.exists:
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


# -- status ----------------------------------------------------------------


def cmd_status(args, out) -> int:
    """0 healthy, 1 degraded, 2 not installed."""
    bundle = Bundle.discover()
    if getattr(args, "json", False):
        return status_json(bundle)

    out.section(f"cmrelay {__version__}")
    out.print(f"  relay commit   {bundle.relay_commit}")
    out.print(f"  closure        {bundle.closure}")
    out.print(f"  config         {paths.INI}")

    if not paths.INI.is_file():
        out.warn(f"{paths.INI} does not exist; this host is not installed")
        out.print("  `cmrelay deploy <domain>` installs one")
        report_masked(out)
        return 2

    config = cfg.read_config()
    out.print(f"  mail domain    {config.mail_domain}")
    out.print(f"  tls mode       {config.tls_cert_mode}")
    out.print(f"  mailboxes      {config.mailboxes_dir}")
    if state.read_intent().get("disable_mail"):
        out.print("  mail           disabled by the operator (--disable-mail)")

    out.section("Components")
    for name in sorted(bundle.components):
        out.print(f"  {name:<16} {bundle.version(name)}")

    out.section("Units")
    units_ok = report_units(out)

    out.section("Conflicts")
    report = run_preflight(bundle, config)
    if report.ok:
        out.ok("  no conflicts")
    else:
        out.print(preflight.format_report(report))

    masked_ok = report_masked(out)
    return 0 if (report.ok and masked_ok and units_ok) else 1


def status_facts(bundle) -> dict:
    facts = {
        "cmrelay": __version__,
        "relay_commit": bundle.relay_commit,
        "closure": str(bundle.closure),
        "installed": paths.INI.is_file(),
        "components": {n: bundle.version(n) for n in sorted(bundle.components)},
        "units": {},
        "masked": {},
        "conflicts": [],
        "disable_mail": bool(state.read_intent().get("disable_mail")),
    }
    if not facts["installed"]:
        return facts
    config = cfg.read_config()
    facts["mail_domain"] = config.mail_domain
    facts["tls_cert_mode"] = config.tls_cert_mode
    facts["mailboxes_dir"] = str(config.mailboxes_dir)
    for name, unit in sorted(unit_report().items()):
        facts["units"][name] = {
            "enabled": unit[0],
            "active": unit[1],
            "healthy": unit[2],
        }
    for name in state.read_lines(paths.DISABLED_UNITS):
        facts["masked"][name] = systemd.query("is-enabled", name) or "-"
    report = run_preflight(bundle, config)
    facts["conflicts"] = [str(f) for f in report.findings]
    return facts


def status_json(bundle) -> int:
    facts = status_facts(bundle)
    print(json.dumps(facts, indent=2, sort_keys=True))
    if not facts["installed"]:
        return 2
    degraded = (
        facts["conflicts"]
        or any(not u["healthy"] for u in facts["units"].values())
        or any(s != "masked" for s in facts["masked"].values())
    )
    return 1 if degraded else 0


def unit_report() -> dict:
    names = (
        sorted(p.name for p in paths.UNITS.glob("*")) if paths.UNITS.is_dir() else []
    )
    states = systemd.unit_states(names)
    report = {}
    for name in names:
        unit = states.get(name)
        enabled = (unit.enabled if unit else "") or "-"
        active = (unit.active if unit else "") or "-"
        report[name] = (enabled, active, healthy(name, enabled, active))
    return report


def report_units(out) -> bool:
    if not systemd.available():
        out.warn("no systemd here")
        return True
    report = unit_report()
    if not report:
        out.warn("no cmrelay units installed")
        return True
    ok = True
    for name, (enabled, active, good) in report.items():
        line = f"  {name:<36} {enabled:<10} {active}"
        if good:
            out.print(line)
        else:
            ok = False
            out.error(line)
    return ok


def healthy(name: str, enabled: str, active: str) -> bool:
    """Timer-driven services are correctly static/inactive."""
    if active == "active":
        return True
    if name.removesuffix(".service") in TIMER_DRIVEN:
        return True
    return enabled in ("disabled", "static", "-")


def report_masked(out) -> bool:
    """What cmrelay masked, and whether it has stayed that way."""
    masked = state.read_lines(paths.DISABLED_UNITS)
    if not masked:
        return True
    out.section("Masked by cmrelay")
    ok = True
    for name in masked:
        enabled = systemd.query("is-enabled", name) or "-"
        if enabled == "masked":
            out.print(f"  {name:<36} masked")
            continue
        ok = False
        out.error(f"  {name:<36} {enabled} -- no longer masked")
        out.print(
            f"      remedy: systemctl disable --now {name} && systemctl mask {name}"
        )
    return ok


# -- version ---------------------------------------------------------------


def cmd_version(args, out) -> int:
    bundle = Bundle.discover()
    out.print(f"cmrelay {__version__}")
    out.print(f"relay {bundle.relay_commit}")
    lock = bundle.lock
    if lock.get("patches"):
        out.print(f"patches: {', '.join(lock['patches'])}")
    keys = release_key_fingerprints(bundle)
    if keys:
        out.print(f"release keys: {', '.join(keys)}")
    out.print("")
    for name in sorted(bundle.components):
        out.print(f"  {name:<16} {bundle.version(name)}")
    return 0


def release_key_fingerprints(bundle) -> list:
    return [f"{path.stem} {fingerprint}" for path, fingerprint in trusted_keys(bundle)]


# -- uninstall -------------------------------------------------------------


def cmd_uninstall(args, out) -> int:
    require_root()
    with fsops.lock():
        return uninstall(args, out)


def uninstall(args, out) -> int:
    recorded = host.config_domain(paths.INI)
    if not args.yes:
        if recorded and args.domain != recorded:
            raise VerbError(
                f"this host serves {recorded}."
                f"\n  Name it to confirm:  cmrelay uninstall {recorded}"
            )
        if not recorded and not args.domain:
            raise VerbError("cmrelay uninstall needs the domain of this relay")

    journal = fsops.Journal()
    out.section("Stopping cmrelay units")
    stop_our_units(out, journal)

    out.section("Unmasking what take-over masked")
    masked = state.read_lines(paths.DISABLED_UNITS)
    for name in masked:
        systemd.unmask(name, reload=False)
        out.print(f"  unmasked {name}")
    if not masked:
        out.print("  nothing to unmask")

    # Enabled, not started: starting here would race the stack being torn
    # down, and relay's unbound deployer runs `systemctl reset-failed`, which
    # answers "not loaded" for a disabled unit.
    out.section("Re-enabling what take-over disabled")
    shadowed = state.read_lines(paths.SHADOWED_UNITS_STATE)
    for name in shadowed:
        systemd.enable(name, reload=False)
        out.print(f"  enabled {name}")
    if not shadowed:
        out.print("  nothing to re-enable")
    out.print("\nStarting them is `cmdeploy run`'s job, not this one's.")

    if systemd.tmpfiles_create():
        out.print("  recreated the /run directories tmpfiles.d declares")

    remove_linked_tools(out, journal)
    diff_configs(out)
    report_own_pages(out)

    out.section("Removing the tree")
    if store.unlink_nix():
        out.print("  removed /nix")
    if journal.remove_directory("cmrelay", paths.ROOT):
        out.print(f"  removed {paths.ROOT}")

    systemd.daemon_reload()
    out.section("Done")
    out.print("Roll back to the Debian stack with: cmdeploy run")
    return 0


def report_own_pages(out):
    """The one thing here with no copy anywhere else."""
    if not paths.WWW_SRC.is_dir():
        return
    pages = sorted(p.name for p in paths.WWW_SRC.iterdir() if p.is_file())
    if not pages:
        return
    out.warn(
        f"{paths.WWW_SRC} holds your own web page source ({', '.join(pages)})"
        " and is about to be removed with the rest of the tree."
    )


def stop_our_units(out, journal):
    if not paths.UNITS.is_dir():
        return
    for unit in sorted(paths.UNITS.glob("*")):
        name = unit.name
        systemd.systemctl("stop", name, check=False)
        systemd.systemctl("disable", "--no-reload", name, check=False)
        journal.remove_file("cmrelay", paths.unit_file(name))
        out.print(f"  stopped and removed {name}")
    for name in activation.AUTHORED_UNITS:
        journal.remove_directory("cmrelay", paths.SYSTEMD_SYSTEM / f"{name}.service.d")


def remove_linked_tools(out, journal):
    """Take back /usr/local/bin, so nothing points into a deleted store."""
    if not paths.LINKED_TOOLS.is_file():
        return
    out.section("Removing tool links")
    for name in paths.LINKED_TOOLS.read_text().split():
        link = paths.LOCAL_BIN / name
        if not is_ours(link):
            continue
        journal.remove_file("tools", link)
        out.print(f"  removed {link}")
    remove_profile_script(out, journal)


def remove_profile_script(out, journal):
    script = paths.PROFILE_SCRIPT
    if not script.is_file():
        return
    if "written by cmrelay" not in script.read_text():
        out.print(f"  leaving {script} alone: cmrelay did not write it")
        return
    journal.remove_file("tools", script)
    out.print(f"  removed {script}")


def is_ours(link: Path) -> bool:
    """Removing by name alone would delete whatever had since taken it."""
    if link.is_symlink():
        return str(link.readlink()).startswith(("/nix/store/", str(paths.ROOT)))
    if link.is_file():
        try:
            return "written by cmrelay" in link.read_text()
        except (OSError, UnicodeDecodeError):
            return False
    return False


def diff_configs(out):
    mine, theirs = paths.INI, paths.CMDEPLOY_INI
    if not (mine.is_file() and theirs.is_file()):
        return
    if mine.read_bytes() == theirs.read_bytes():
        return
    out.warn(
        f"{mine} differs from {theirs}."
        " `cmdeploy run` regenerates its own ini from its checkout, so"
        " configuration edits made under cmrelay will not survive a rollback."
    )


# -- dispatch --------------------------------------------------------------


def cmd_dns(args, out) -> int:
    return dns_verb(args, Bundle.discover(), out)


def cmd_upgrade(args, out) -> int:
    require_root()
    with fsops.lock():
        return upgrade_verb(args, Bundle.discover(), out)


def cmd_installer(args, out) -> int:
    return installer_verb(args, out)


VERBS = {
    "deploy": cmd_deploy,
    "dns": cmd_dns,
    "installer": cmd_installer,
    "status": cmd_status,
    "uninstall": cmd_uninstall,
    "upgrade": cmd_upgrade,
    "version": cmd_version,
}


def dispatch(args, out) -> int:
    return VERBS[args.verb](args, out)
