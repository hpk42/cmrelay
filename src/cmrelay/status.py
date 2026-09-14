"""`cmrelay status` and `cmrelay version`: report, never modify."""

from . import config as cfg
from . import paths, preflight, state, systemd
from .bundle import Bundle
from .deploy import TIMER_DRIVEN
from .signing import trusted_keys
from .version import __version__


def status_verb(args, out) -> int:
    """0 healthy, 1 degraded, 2 not installed."""
    bundle = Bundle.discover()
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
    report = preflight.run_preflight(bundle, config)
    if report.ok:
        out.ok("  no conflicts")
    else:
        out.print(preflight.format_report(report))

    masked_ok = report_masked(out)
    return 0 if (report.ok and masked_ok and units_ok) else 1


def report_units(out) -> bool:
    if not systemd.available():
        out.warn("no systemd here")
        return True
    names = (
        sorted(p.name for p in paths.UNITS.glob("*")) if paths.UNITS.is_dir() else []
    )
    if not names:
        out.warn("no cmrelay units installed")
        return True
    states = systemd.unit_states(names)
    ok = True
    for name in names:
        unit = states.get(name)
        enabled = (unit.enabled if unit else "") or "-"
        active = (unit.active if unit else "") or "-"
        line = f"  {name:<36} {enabled:<10} {active}"
        if healthy(name, enabled, active):
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


def version_verb(args, out) -> int:
    bundle = Bundle.discover()
    out.print(f"cmrelay {__version__}")
    out.print(f"relay {bundle.relay_commit}")
    patches = bundle.lock.get("patches")
    if patches:
        out.print(f"patches: {', '.join(patches)}")
    keys = [f"{path.stem} {print_}" for path, print_ in trusted_keys(bundle)]
    if keys:
        out.print(f"release keys: {', '.join(keys)}")
    out.print("")
    for name in sorted(bundle.components):
        out.print(f"  {name:<16} {bundle.version(name)}")
    return 0
