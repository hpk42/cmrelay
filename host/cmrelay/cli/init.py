"""`cmrelay init`."""

import shutil

from chatmaild.config import write_initial_config

from .. import CmrelayError, paths
from ..stack import config as cfg
from ..system.host import Host


def init_domain(given: str | None) -> str:
    """The domain to initialise for; an existing relay keeps its own."""
    if not paths.CMDEPLOY_INI.is_file():
        if given:
            return given
        raise CmrelayError(
            "init needs a domain on a host with no configuration."
            "\n\n      cmrelay init chat.example.org"
        )
    recorded = cfg.read_config(paths.CMDEPLOY_INI).mail_domain
    if not given or given == recorded:
        return recorded
    raise CmrelayError(
        f"this host is a cmdeploy relay for '{recorded}', not '{given}'."
        "\n\n  Changing the mail domain strands the existing accounts"
        "\n  and invalidates the published DKIM record."
        "\n\n  `cmrelay init` without a domain adopts its chatmail.ini."
    )


def init_verb(args, bundle, out) -> int:
    """Write chatmail.ini, or adopt the one of a cmdeploy relay."""
    if paths.INI.is_file():
        raise CmrelayError(f"{paths.INI} exists, not modifying it")
    domain = init_domain(args.domain)
    paths.ROOT.mkdir(parents=True, exist_ok=True)
    if paths.CMDEPLOY_INI.is_file():
        shutil.copyfile(paths.CMDEPLOY_INI, paths.INI)
        out.print(f"adopted {paths.CMDEPLOY_INI} as {paths.INI}")
    else:
        write_initial_config(paths.INI, domain, {})
        out.print(f"created {paths.INI} for {domain}")

    config = cfg.read_config(paths.INI)
    for warning in cfg.warnings(config, paths.INI):
        out.warn(warning)
    if Host.classify().takes_over:
        out.print("\nNext you can take over the cmdeploy-relay by running:")
        out.print("\n    cmrelay deploy")
        return 0
    if config.tls_cert_mode == "acme":
        out.print(f"\nBefore deploying, publish A/AAAA records for {domain}, and:\n")
        out.print(f"    www.{domain}. 3600 IN CNAME {domain}.")
        out.print(f"    mta-sts.{domain}. 3600 IN CNAME {domain}.")
    out.print("\nNext, after any edits to the file:\n\n    cmrelay deploy")
    return 0
