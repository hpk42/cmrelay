"""install, status and uninstall.

The four verbs differ mainly in what they are allowed to touch.

`install` is for fresh hosts. It runs the preflight and bails out on any
conflicting unit or occupied port having modified nothing at all, because a
half-installed mail server is worse than none.

The one thing it does take over is the resolver: every stock Debian and
Fedora has systemd-resolved on port 53, cmdeploy stops and masks it as an
ordinary part of every deploy, and refusing over it would make `install`
useless on exactly the hosts it targets. That is recorded for uninstall to
reverse. Any other holder of port 53 still refuses.

`install --take-over` is the only way to touch existing services, and the only
one that may act on a conflict. When it does it stops, disables *and masks*:
disabling alone loses to the next apt or dnf upgrade re-enabling the unit.

`status` re-runs the detector and modifies nothing, so a conflict introduced
later -- a distribution upgrade re-enabling postfix -- becomes visible
instead of silently fighting for port 25.

`uninstall` reverses exactly what --take-over did. The unmask is load-bearing
part: `systemctl enable` fails on a masked unit, so a `cmdeploy run` against
a host still carrying cmrelay's masks would not come back up.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

# chatmaild is a hard dependency and deliberately not a PyPI one: it is
# relay's code, built from the vendored commit into the same interpreter as
# the rest of the closure. Importing it here is what makes cmrelay render
# relay's templates with relay's own Config rather than a copy of it.
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


def read_config(bundle, inipath: Path = paths.INI):
    """relay's own Config, read by relay's own code."""
    return _read_config(inipath)


def write_initial_config(mail_domain: str, overrides: dict | None = None):
    """The same function `cmdeploy init` calls, so the ini is relay's, not ours."""
    paths.INI.parent.mkdir(parents=True, exist_ok=True)
    _write_initial_config(paths.INI, mail_domain, overrides or {})


# ----------------------------------------------------------------------
# install
# ----------------------------------------------------------------------


def cmd_install(args, out) -> int:
    """One entry point, because an operator should not have to know in advance
    which kind of host they are on. The preflight already knows, so the
    refusal names the flag rather than a second verb."""
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
    """Everything from chatmail.ini onwards, which both paths share.

    They differ only in what they may do to a host *before* this point: a
    plain install refuses to touch anything, --take-over stops and masks what
    is in the way. From here they are the same run.
    """
    if config is None:
        config = read_config(bundle)
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

    Installing the store empties the staging tree this process is running
    out of, so everything it has not imported yet would vanish underneath
    it -- chatmaild's own data files first. Re-execing the installed CLI is
    the boundary: from here on, every path resolves to what the binaries
    were built for.
    """
    if bundle.prefix is None:
        return
    out.section("Installing the closure")
    stage_store = bundle.prefix / "nix/store"
    paths.ROOT.mkdir(parents=True, exist_ok=True)
    # Before the first rename rather than after the last. install() moves
    # store paths out of the staging tree, so a run interrupted halfway
    # through leaves a tree that is missing some of them; dropping the marker
    # first is what stops the stub offering that husk to the next run as
    # though it were whole.
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

    An install cannot finish the job on its own: the relay needs DNS pointing
    at it before acmetool can complete a challenge, and mail sent from it is
    worthless without SPF, DKIM and DMARC. `cmdeploy dns` generates a full
    zone file from a live host; this is the short version, printed where the
    operator will see it.
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


# ----------------------------------------------------------------------
# install --take-over
# ----------------------------------------------------------------------


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
            systemd.record_disabled_units(masked)
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
    config = read_config(bundle)
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
            # Reported, not warned about. Two commit hashes say which is
            # newer only against a shared history, and there is no relay
            # checkout here to ask -- the host's marker may name a branch,
            # a fork or a tree cmrelay has never seen. Differing is also the
            # normal case, since a cmrelay release pins one relay commit and
            # a deployed host tracks whatever it was deployed from.
            out.print(f"  cmrelay vendored relay {bundle.relay_commit[:12]}")
    else:
        findings["host_relay_commit"] = None
        out.print(f"  {marker}: not found")

    return findings


def mask_existing(out) -> list[str]:
    """Take the existing stack out of the way. Returns what was masked.

    Two different treatments, because masking a name cmrelay wants to use
    would make that name unusable: `systemctl mask` writes
    /etc/systemd/system/<name> -> /dev/null, which is the exact path
    cmrelay's own unit symlink needs.
    """
    # One call, not thirteen. unit_states enumerates every unit file on the
    # host to learn which of these exist, and `systemctl list-unit-files`
    # costs 942 ms on a two-core VPS -- so asking per unit spent twelve
    # seconds of a take-over re-reading the same answer.
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
        systemd.record_shadowed_units(shadowed)

    for name in preflight.MASKED_UNITS + preflight.RESOLVER_UNITS:
        if name not in states or states[name][0] == "masked":
            continue
        enabled, active = states[name]
        out.print(f"  {name} ({enabled}/{active}) -> stopped, disabled, masked")
        systemd.stop_disable_mask(name, reload=False)
        masked.append(name)

    # The one reload the thirteen above deferred. Nothing has read the unit
    # graph in between: cmrelay writes its own units afterwards and activate()
    # reloads again before starting anything.
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


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------


def cmd_status(args, out) -> int:
    bundle = Bundle.discover()

    out.section(f"cmrelay {version_line(bundle)}")
    out.print(f"  relay commit   {bundle.relay_commit}")
    out.print(f"  closure        {bundle.closure}")
    out.print(f"  config         {paths.INI}")

    if not paths.INI.is_file():
        out.warn(f"{paths.INI} does not exist; this host is not installed")
        out.print("  `cmrelay install --mail-domain <domain>` installs one")
        # Everything below reports on an installation, and there is none. The
        # conflict check especially: on a host cmrelay has never touched it
        # lists the distribution's own services and advises masking them,
        # which is advice for an install nobody asked for. `install` already
        # refuses and changes nothing when something is in the way, so the
        # question "would it install here" is answered by running it.
        #
        # What is still worth printing is anything upgrade masked. An upgrade
        # that stopped before writing the ini leaves masked units and no other
        # record of them, and that is exactly when the list is needed.
        report_masked(out)
        return 1

    config = read_config(bundle)
    out.print(f"  mail domain    {config.mail_domain}")
    out.print(f"  tls mode       {config.tls_cert_mode}")
    out.print(f"  mailboxes      {config.mailboxes_dir}")

    out.section("Components")
    for name in sorted(bundle.components):
        out.print(f"  {name:<16} {bundle.version(name)}")

    out.section("Units")
    report_units(out)

    # Not "Preflight". The detector is the one install uses, but the question
    # it answers here is different: nothing is about to take off, so this is
    # asking whether something has appeared since. A distribution package
    # installed later, or a distro upgrade re-enabling systemd-resolved, does
    # not disturb a running relay -- it collides at the next reboot, which is
    # the worst moment to find out.
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

    Checked rather than listed, because nothing else here would notice one
    coming back. These are the units cmrelay takes over instead of refusing
    on -- systemd-resolved and the other resolver managers -- so they are
    deliberately absent from CONFLICTING_UNITS: an install that refused
    merely because systemd-resolved was running could never proceed on a
    stock Debian. The cost of that choice is that the conflict detector is
    blind to them afterwards, and this is where it is paid back.

    A resolver that has been re-enabled disturbs nothing today. It takes
    /etc/resolv.conf at the next boot, and a relay that cannot resolve
    anything is a relay that cannot deliver mail.
    """
    masked = systemd.read_disabled_units()
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

    Not everything is meant to be running. The expire and fsreport services
    are started by their timers, so `static`/`inactive` is the correct state
    for them and flagging it would train the reader to ignore the report.
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
        # Before the ini exists, resolve config-dependent ports against
        # relay's own defaults rather than skipping those entries.
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


def version_line(bundle) -> str:
    return __version__


def cmd_version(args, out) -> int:
    """The runtime face of vendor.lock, and what a bug report should paste."""
    bundle = Bundle.discover()
    out.print(f"cmrelay {version_line(bundle)}")
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
    masked = systemd.read_disabled_units()
    for name in masked:
        systemd.unmask(name, reload=False)
        out.print(f"  unmasked {name}")
    if not masked:
        out.print("  nothing to unmask")

    # Enabled, not started: `cmdeploy run` starts what it wants, and starting
    # a stack here would race the one being torn down. Enabling is not
    # cosmetic -- relay's unbound deployer runs
    #
    #     systemctl reset-failed unbound.service
    #
    # through pyinfra's server.shell, which fails the host on a non-zero
    # exit, and systemd answers "Unit unbound.service not loaded" for a unit
    # that is disabled. So a host left disabled cannot be rolled back at all:
    # `cmdeploy run` aborts before it reaches its own enable step.
    out.section("Re-enabling what --take-over disabled")
    shadowed = systemd.read_shadowed_units()
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

    Removing by name alone would delete whatever had since taken the name.
    Both shapes cmrelay writes are recognisable: a symlink into the store,
    and a wrapper carrying the line it generates.
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
    ours, theirs = paths.INI, paths.CMDEPLOY_INI
    if not (ours.is_file() and theirs.is_file()):
        return
    if ours.read_bytes() == theirs.read_bytes():
        return
    out.warn(
        f"{ours} differs from {theirs}."
        " `cmdeploy run` regenerates its own ini from its checkout, so"
        " configuration edits made under cmrelay will not survive a rollback."
    )


# ----------------------------------------------------------------------
# dispatch
# ----------------------------------------------------------------------

VERBS = {
    "install": cmd_install,
    "status": cmd_status,
    "uninstall": cmd_uninstall,
    "version": cmd_version,
}


def dispatch(args, out) -> int:
    return VERBS[args.verb](args, out)
