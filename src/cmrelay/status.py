"""`cmrelay status` and `cmrelay version`: report, never modify.

status re-runs the conflict detector and reads unit state; its --json
form emits the same facts as one object. Exit codes are the contract:
0 healthy, 1 degraded, 2 not installed.
"""

import json

from . import config as cfg
from . import paths, preflight, state, systemd
from .bundle import Bundle
from .deploy import TIMER_DRIVEN
from .signing import trusted_keys
from .version import __version__


def status_verb(args, out) -> int:
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
    report = preflight.run_preflight(bundle, config)
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
    report = preflight.run_preflight(bundle, config)
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


def version_verb(args, out) -> int:
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
