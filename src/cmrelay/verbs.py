"""install, status and uninstall.

The verbs differ mainly in what they are allowed to touch. `install` bails
out on any conflict having modified nothing, except the resolver: every stock
Debian and Fedora has systemd-resolved on port 53, so refusing over it would
make `install` useless on exactly the hosts it targets.

`install --take-over` is the only verb that may act on a conflict, `status`
modifies nothing, and `uninstall` reverses what --take-over did.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

# Deliberately not a PyPI dependency: relay's own code, built from the
# vendored commit into the same interpreter as the rest of the closure.
from chatmaild.config import read_config as _read_config
from chatmaild.config import write_initial_config as _write_initial_config

from . import paths, preflight, store, systemd, users
from .bundle import ENV_CLOSURE, ENV_STAGE, READY_MARKER, Bundle
from .deploy import AUTHORED_UNITS, TIMER_DRIVEN, Deployment
from .version import __version__

# Set across the re-exec that follows installing the store, so the second
# half of an install does not re-run a check the first half already passed.
ENV_PREFLIGHT_PASSED = "CMRELAY_PREFLIGHT_PASSED"


class VerbError(Exception):
    pass


def require_root():
    if os.geteuid() != 0:
        raise VerbError("cmrelay must run as root")


def load_port_table(bundle) -> list:
    """relay's port table, as extracted at build time and shipped in the closure."""
    data = json.loads((bundle.root / "relay-pins.json").read_text())
    return data["port_table"]


def read_config(inipath: Path = paths.INI):
    """relay's own Config, read by relay's own code."""
    return _read_config(inipath)


def write_initial_config(mail_domain: str, overrides: dict | None = None):
    """The same function `cmdeploy init` calls, so the ini is relay's, not ours."""
    paths.INI.parent.mkdir(parents=True, exist_ok=True)
    _write_initial_config(paths.INI, mail_domain, overrides or {})


def cmd_install(args, out) -> int:
    """One entry point: the preflight already knows which kind of host this
    is, so the refusal names the flag rather than a second verb."""
    require_root()
    bundle = Bundle.discover()
    if args.take_over:
        return install_over_existing(args, bundle, out)
    return install_fresh(args, bundle, out)


def install_fresh(args, bundle, out) -> int:
    if paths.INI.exists() and not args.force:
        out.error(f"{paths.INI} already exists: this host has cmrelay on it already.")
        out.print("Use --force to overwrite it.")
        return 1

    if not os.environ.get(ENV_PREFLIGHT_PASSED):
        out.section("Preflight")
        report = run_preflight(bundle, out, mail_domain=args.mail_domain)
        if not report.ok:
            out.error("this host is not a fresh install target")
            takeover = paths.CMDEPLOY_INI.is_file()
            out.print(preflight.format_report(report, remedies=not takeover))
            out.print("\nNothing was modified.")
            if takeover:
                out.print(
                    f"This host already runs chatmail ({paths.CMDEPLOY_INI}"
                    " exists).\nTo convert it, re-run with --take-over."
                )
            else:
                out.print(
                    "If this host is meant to keep the stack above, cmrelay is"
                    " not\nfor it. To hand it over instead, re-run with"
                    " --take-over."
                )
            return 1
        out.ok("  no conflicting units, no occupied ports, no existing store")

    install_store(bundle, out)

    out.section("Creating users and directories")
    created = users.ensure_required_users()
    if created:
        out.print(f"  created {', '.join(created)}")

    out.section("Writing configuration")
    if args.config:
        source = Path(args.config)
        if not source.is_file():
            raise VerbError(f"--config {source} does not exist")
        paths.INI.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, paths.INI)
        out.print(f"  copied {source} -> {paths.INI}")
    else:
        write_initial_config(args.mail_domain, parse_overrides(args.set))
        out.print(f"  {paths.INI}")

    config = apply_configuration(bundle, args, out)
    out.section("Done")
    report_dns(config, bundle, out)
    return 0


def apply_configuration(bundle, args, out, config=None):
    """Everything from chatmail.ini onwards, which both install paths share."""
    if config is None:
        config = read_config()
    deployment = Deployment(bundle, config, out, disable_mail=args.disable_mail)
    deployment.configure()
    deployment.install_daemon_units()

    out.section("Starting services")
    deployment.activate()
    link_cli(bundle, out)
    return config


def parse_overrides(pairs) -> dict:
    overrides = {}
    for item in pairs or []:
        if "=" not in item:
            raise VerbError(f"--set expects key=value, got {item!r}")
        key, value = item.split("=", 1)
        overrides[key.strip()] = value.strip()
    return overrides


def install_store(bundle, out):
    """Move the closure into place, then continue from the installed copy.

    Installing empties the staging tree this process runs out of, so anything
    not yet imported would vanish underneath it. The re-exec is the boundary.
    """
    if bundle.prefix is None:
        return
    out.section("Installing the closure")
    stage_store = bundle.prefix / "nix/store"
    paths.ROOT.mkdir(parents=True, exist_ok=True)
    # Before the first rename, so an interrupted run leaves no marker on a
    # staging tree the stub would otherwise offer to the next run as whole.
    (bundle.prefix / READY_MARKER).unlink(missing_ok=True)
    moved = store.install(stage_store)
    store.link_nix()
    note = store.label_for_selinux()
    if note:
        out.print(f"  {note}")
    out.print(f"  {moved} store paths under {paths.STORE}, /nix -> {paths.NIX}")
    exec_installed(bundle, out)


def exec_installed(bundle, out):
    """Hand over to the CLI now living in the store. Does not return."""
    installed = Path(bundle.closure) / "bin/cmrelay"
    if not installed.is_file():
        raise VerbError(f"the installed closure has no CLI at {installed}")
    env = dict(os.environ)
    # Bundle.discover() keys off these; without them it locates the closure
    # from its own __file__, which is now the installed copy.
    env.pop(ENV_STAGE, None)
    env.pop(ENV_CLOSURE, None)
    env.pop("PYTHONPATH", None)
    env[ENV_PREFLIGHT_PASSED] = "1"
    out.print("  continuing from the installed store")
    sys.stdout.flush()
    os.execve(str(installed), [str(installed), *sys.argv[1:]], env)


def report_dns(config, bundle, out):
    """The records the operator still has to publish.

    acmetool cannot complete a challenge until DNS points here, and mail is
    worthless without SPF, DKIM and DMARC.
    """
    domain = config.mail_domain
    out.print(f"\nPublish these records for {domain}:\n")
    out.print(f"  {domain}.            A      <this host's IPv4>")
    out.print(f"  {domain}.            AAAA   <this host's IPv6>")
    out.print(f"  mta-sts.{domain}.    CNAME  {domain}.")
    out.print(f"  www.{domain}.        CNAME  {domain}.")
    out.print(f"  {domain}.            MX     10 {domain}.")
    out.print(f'  {domain}.            TXT    "v=spf1 a:{domain} -all"')
    out.print(
        f'  _dmarc.{domain}.     TXT    "v=DMARC1;p=reject;rua=mailto:postmaster@{domain}"'
    )
    out.print(f'  _mta-sts.{domain}.   TXT    "v=STSv1; id=<any changing value>"')

    dkim = paths.DKIM_KEYS / "opendkim.txt"
    if dkim.is_file():
        out.print(f"\n  the DKIM record, from {dkim}:\n")
        for line in dkim.read_text().splitlines():
            out.print(f"    {line}")

    out.print(
        f"\n`cmdeploy dns` in a relay checkout generates the full zone file"
        f"\nfor {domain} and checks what is already published."
    )


def link_cli(bundle, out):
    """Give the CLI a name that survives the next release."""
    paths.BIN.mkdir(parents=True, exist_ok=True)
    target = Path(bundle.closure) / "bin/cmrelay"
    if paths.CLI.is_symlink() or paths.CLI.exists():
        paths.CLI.unlink()
    paths.CLI.symlink_to(target)
    out.print(f"  {paths.CLI} -> {target}")


def install_over_existing(args, bundle, out) -> int:
    # install_store() ends in a re-exec of this same verb from the installed
    # closure. Without this the second run would prompt again and, having
    # been answered, stop and mask a stack it had already taken over.
    resumed = bool(os.environ.get(ENV_PREFLIGHT_PASSED))

    if not resumed:
        out.section("What is on this host")
        findings = describe_existing(bundle, out)

        conflicts = preflight.check_units(ours=ours)
        if not conflicts.ok:
            out.print("\nUnits cmrelay will stop, disable and mask:")
            for finding in conflicts.findings:
                out.print(f"  {finding.subject} ({finding.detail})")

        if not args.yes:
            out.print("")
            answer = input("Convert this host to cmrelay? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                out.print("aborted; nothing was modified")
                return 1

        out.section("Stopping the existing stack")
        masked = mask_existing(out)
        if masked:
            systemd.record_units(paths.DISABLED_UNITS, masked)
    else:
        findings = describe_existing(bundle, out)

    install_store(bundle, out)

    out.section("Users")
    missing = users.check_all_present()
    if missing:
        # A cmdeploy host has these already; a partial one may not.
        out.print(f"  creating missing accounts: {', '.join(missing)}")
        users.ensure_required_users()
    else:
        out.print("  all present; reusing their existing uids")

    out.section("Configuration")
    adopt_config(out, findings)
    config = read_config()
    users.verify_mailbox_ownership(config.mailboxes_dir)

    apply_configuration(bundle, args, out, config=config)
    out.section("Verifying")
    verify(bundle, config, out)
    return 0


def describe_existing(bundle, out) -> dict:
    """Report the markers a cmdeploy host leaves, as warnings rather than gates.

    The operator knows their host better than any detector does.
    """
    findings = {}

    ini = paths.CMDEPLOY_INI
    findings["cmdeploy_ini"] = ini if ini.is_file() else None
    out.print(f"  {ini}: {'found' if findings['cmdeploy_ini'] else 'not found'}")

    marker = paths.VERSION_MARKER
    if marker.is_file():
        text = marker.read_text()
        commit = text.split("\n", 1)[0].strip()
        findings["host_relay_commit"] = commit
        out.print(f"  {marker}: relay {commit[:12]}")
        if len(text.splitlines()) > 1 and text.splitlines()[1].startswith("diff"):
            out.warn(
                "this host was deployed from a modified working tree;"
                " its configuration may not match any relay commit"
            )
        if commit and commit != bundle.relay_commit:
            # Reported, not warned about: two hashes say which is newer only
            # against a shared history, and differing is the normal case.
            out.print(f"  cmrelay vendored relay {bundle.relay_commit[:12]}")
    else:
        findings["host_relay_commit"] = None
        out.print(f"  {marker}: not found")

    return findings


def mask_existing(out) -> list[str]:
    """Take the existing stack out of the way. Returns what was masked.

    Two treatments: see preflight.SHADOWED_UNITS for why the names cmrelay
    reuses are shadowed rather than masked.
    """
    # One call, not thirteen: asking per unit spent twelve seconds of a
    # take-over re-reading the same answer.
    states = preflight.unit_states(
        preflight.SHADOWED_UNITS + preflight.MASKED_UNITS + preflight.RESOLVER_UNITS
    )

    masked = []
    shadowed = []
    for name in preflight.SHADOWED_UNITS:
        if ours(name) or name not in states:
            continue
        enabled, active = states[name]
        out.print(
            f"  {name} ({enabled}/{active}) -> stopped and disabled, then shadowed"
        )
        if enabled in ("enabled", "enabled-runtime"):
            shadowed.append(name)
        systemd.ensure_service(name, running=False, enabled=False, reload=False)
        systemd.unmask(name, reload=False)
    if shadowed:
        systemd.record_units(paths.SHADOWED_UNITS_STATE, shadowed)

    for name in preflight.MASKED_UNITS + preflight.RESOLVER_UNITS:
        if name not in states or states[name][0] == "masked":
            continue
        enabled, active = states[name]
        out.print(f"  {name} ({enabled}/{active}) -> stopped, disabled, masked")
        systemd.stop_disable_mask(name, reload=False)
        masked.append(name)

    # The one reload the units above deferred.
    systemd.daemon_reload()
    return masked


def ours(name: str) -> bool:
    """Whether this unit name is already served by a cmrelay unit file."""
    return (paths.UNITS / name).is_symlink()


def adopt_config(out, findings):
    """chatmail.ini is copied exactly once, and the original is left alone.

    From then on /opt/cmrelay/chatmail.ini is cmrelay's source of truth. The
    cmdeploy copy stays put so rollback finds it where it was.
    """
    if paths.INI.exists():
        out.print(f"  {paths.INI} already exists; leaving it alone")
        return
    source = findings.get("cmdeploy_ini")
    if not source:
        raise VerbError(
            f"no {paths.CMDEPLOY_INI} to adopt and no {paths.INI} yet."
            " This host has no chatmail deployment to take over;"
            " use `cmrelay install --mail-domain <domain>`."
        )
    paths.INI.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, paths.INI)
    out.print(f"  copied {source} -> {paths.INI}")


def verify(bundle, config, out):
    """Check that DKIM signing and TLS actually work, rather than assuming."""
    problems = []

    if not config.ipv4_relay:
        key = paths.DKIM_KEYS / "opendkim.private"
        if not key.is_file():
            problems.append(f"no DKIM key at {key}")
        elif (
            systemd.available()
            and systemd.query("is-active", "opendkim.service") != "active"
        ):
            problems.append("opendkim.service is not running")

    for path in (config.tls_cert_path, config.tls_key_path):
        if not Path(path).is_file():
            problems.append(f"no TLS material at {path}")

    if systemd.available():
        for unit in ("dovecot.service", "postfix.service", "nginx.service"):
            if systemd.query("is-active", unit) != "active":
                problems.append(f"{unit} is not running")

    if problems:
        for problem in problems:
            out.error(f"  {problem}")
        out.print("\nThe host is converted but not healthy. `cmrelay status` has more.")
    else:
        out.ok("  DKIM key present, TLS material present, services running")


def cmd_status(args, out) -> int:
    bundle = Bundle.discover()

    out.section(f"cmrelay {__version__}")
    out.print(f"  relay commit   {bundle.relay_commit}")
    out.print(f"  closure        {bundle.closure}")
    out.print(f"  config         {paths.INI}")

    if not paths.INI.is_file():
        out.warn(f"{paths.INI} does not exist; this host is not installed")
        out.print("  `cmrelay install --mail-domain <domain>` installs one")
        # Everything below reports on an installation, and there is none;
        # the conflict check would just advise masking the distribution's own
        # services. What masked units remain is still worth printing, because
        # an upgrade that stopped before writing the ini leaves no other
        # record of them.
        report_masked(out)
        return 1

    config = read_config()
    out.print(f"  mail domain    {config.mail_domain}")
    out.print(f"  tls mode       {config.tls_cert_mode}")
    out.print(f"  mailboxes      {config.mailboxes_dir}")

    out.section("Components")
    for name in sorted(bundle.components):
        out.print(f"  {name:<16} {bundle.version(name)}")

    out.section("Units")
    report_units(out)

    # Not "Preflight": the same detector, but asking whether something has
    # appeared since. It collides at the next reboot, not today.
    out.section("Conflicts")
    report = run_preflight(bundle, out, config=config)
    if report.ok:
        out.ok("  no conflicts")
    else:
        out.print(preflight.format_report(report))

    masked_ok = report_masked(out)
    return 0 if report.ok and masked_ok else 1


def report_masked(out) -> bool:
    """What cmrelay masked, and whether it has stayed that way.

    The resolver units are absent from CONFLICTING_UNITS, because refusing
    over systemd-resolved would never proceed on a stock Debian. The detector
    is blind to them afterwards, and this is where that is paid back.
    """
    masked = systemd.read_units(paths.DISABLED_UNITS)
    if not masked:
        return True
    out.section("Masked by cmrelay")
    ok = True
    for name in masked:
        state = systemd.query("is-enabled", name) or "-"
        if state == "masked":
            out.print(f"  {name:<36} masked")
            continue
        ok = False
        out.error(f"  {name:<36} {state} -- no longer masked")
        out.print(
            f"      remedy: systemctl disable --now {name} && systemctl mask {name}"
        )
    return ok


def report_units(out):
    if not systemd.available():
        out.warn("no systemd here")
        return
    names = (
        sorted(p.name for p in paths.UNITS.glob("*")) if paths.UNITS.is_dir() else []
    )
    if not names:
        out.warn("no cmrelay units installed")
        return
    for name in names:
        enabled = systemd.query("is-enabled", name) or "-"
        active = systemd.query("is-active", name) or "-"
        line = f"  {name:<36} {enabled:<10} {active}"
        if healthy(name, enabled, active):
            out.print(line)
        else:
            out.error(line)


def healthy(name: str, enabled: str, active: str) -> bool:
    """Whether a unit being in this state is what cmrelay intended.

    Timer-driven services are correctly `static`/`inactive`, and flagging
    them would train the reader to ignore the report.
    """
    if active == "active":
        return True
    if name.removesuffix(".service") in TIMER_DRIVEN:
        return True
    return enabled in ("disabled", "static", "-")


def run_preflight(bundle, out, config=None, mail_domain=None):
    """Resolve relay's port table against this host's config, then look."""
    table = load_port_table(bundle)
    if config is None:
        # Before the ini exists, resolve against relay's defaults rather
        # than skipping the config-dependent entries.
        config = default_config(mail_domain or "example.org")
    if isinstance(config, dict):
        config = SimpleNamespace(**config)
    resolved = preflight.resolve_port_table(table, config)
    return preflight.run(resolved, ss_argv=bundle.command("iproute2", "ss"), ours=ours)


def default_config(mail_domain: str):
    """A Config from relay's own defaults, for a host that has no ini yet."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chatmail.ini"
        _write_initial_config(path, mail_domain, {})
        return _read_config(path)


def cmd_version(args, out) -> int:
    """The runtime face of vendor.lock, and what a bug report should paste."""
    bundle = Bundle.discover()
    out.print(f"cmrelay {__version__}")
    out.print(f"relay {bundle.relay_commit}")
    lock = bundle.lock
    if lock.get("patches"):
        out.print(f"patches: {', '.join(lock['patches'])}")
    out.print("")
    for name in sorted(bundle.components):
        out.print(f"  {name:<16} {bundle.version(name)}")
    return 0


def cmd_uninstall(args, out) -> int:
    require_root()

    if not args.yes:
        answer = (
            input(f"Remove cmrelay from this host ({paths.ROOT} and /nix)? [y/N] ")
            .strip()
            .lower()
        )
        if answer not in ("y", "yes"):
            out.print("aborted")
            return 1

    out.section("Stopping cmrelay units")
    stop_our_units(out)

    out.section("Unmasking what --take-over masked")
    masked = systemd.read_units(paths.DISABLED_UNITS)
    for name in masked:
        systemd.unmask(name, reload=False)
        out.print(f"  unmasked {name}")
    if not masked:
        out.print("  nothing to unmask")

    # Enabled, not started: starting here would race the stack being torn
    # down. Enabling is not cosmetic, though -- relay's unbound deployer runs
    # `systemctl reset-failed unbound.service` through pyinfra's server.shell,
    # and systemd answers "not loaded" for a disabled unit, failing the host
    # before `cmdeploy run` reaches its own enable step.
    out.section("Re-enabling what --take-over disabled")
    shadowed = systemd.read_units(paths.SHADOWED_UNITS_STATE)
    for name in shadowed:
        systemd.enable(name, reload=False)
        out.print(f"  enabled {name}")
    if not shadowed:
        out.print("  nothing to re-enable")
    out.print("\nStarting them is `cmdeploy run`'s job, not this one's.")

    if systemd.tmpfiles_create():
        out.print("  recreated the /run directories tmpfiles.d declares")

    remove_linked_tools(out)
    diff_configs(out)

    out.section("Removing the tree")
    if store.unlink_nix():
        out.print("  removed /nix")
    if paths.ROOT.exists():
        shutil.rmtree(paths.ROOT)
        out.print(f"  removed {paths.ROOT}")

    systemd.daemon_reload()
    out.section("Done")
    out.print("Roll back to the Debian stack with: cmdeploy run")
    return 0


def stop_our_units(out):
    if not paths.UNITS.is_dir():
        return
    for unit in sorted(paths.UNITS.glob("*")):
        name = unit.name
        systemd.ensure_service(name, running=False, enabled=False)
        real = paths.unit_file(name)
        if real.is_file() or real.is_symlink():
            real.unlink()
        out.print(f"  stopped and removed {name}")
    for name in AUTHORED_UNITS:
        dropin = paths.SYSTEMD_SYSTEM / f"{name}.service.d"
        if dropin.is_dir():
            shutil.rmtree(dropin)


def remove_linked_tools(out):
    """Take back /usr/local/bin, so nothing points into a deleted store."""
    if not paths.LINKED_TOOLS.is_file():
        return
    out.section("Removing tool links")
    for name in paths.LINKED_TOOLS.read_text().split():
        link = paths.LOCAL_BIN / name
        if not is_ours(link):
            continue
        link.unlink()
        out.print(f"  removed {link}")
    remove_profile_script(out)


def remove_profile_script(out):
    """Take /opt/cmrelay/bin back off PATH, so it does not point at nothing."""
    script = paths.PROFILE_SCRIPT
    if not script.is_file():
        return
    if "written by cmrelay" not in script.read_text():
        out.print(f"  leaving {script} alone: cmrelay did not write it")
        return
    script.unlink()
    out.print(f"  removed {script}")


def is_ours(link: Path) -> bool:
    """Whether cmrelay put this in /usr/local/bin, or something else did.

    Removing by name alone would delete whatever had since taken it.
    """
    if link.is_symlink():
        return str(link.readlink()).startswith(("/nix/store/", str(paths.ROOT)))
    if link.is_file():
        try:
            return "written by cmrelay" in link.read_text()
        except (OSError, UnicodeDecodeError):
            return False
    return False


def diff_configs(out):
    """A courtesy: say what a rollback would lose."""
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


VERBS = {
    "install": cmd_install,
    "status": cmd_status,
    "uninstall": cmd_uninstall,
    "version": cmd_version,
}


def dispatch(args, out) -> int:
    return VERBS[args.verb](args, out)
