"""The cmrelay command line."""

import argparse
import os
import sys

from .. import CmrelayError
from ..release.bundle import Bundle
from ..system import fsops
from .deploy import deploy_verb
from .dns.dns import dns_verb
from .init import init_verb
from .installer import installer_verb
from .output import Out
from .status import status_verb
from .uninstall import uninstall_verb
from .upgrade import upgrade_verb

VERBS = {
    "init": init_verb,
    "deploy": deploy_verb,
    "dns": dns_verb,
    "installer": installer_verb,
    "status": status_verb,
    "uninstall": uninstall_verb,
    "upgrade": upgrade_verb,
}
# These change the host. The installer does not take the lock:
# a refused installer must leave no lock file behind.
# upgrade takes it itself, around everything but the deploys it runs
LOCKED = ("init", "deploy", "uninstall")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="cmrelay",
        description="Install and maintain a chatmail relay.",
        epilog="The manual is at https://cmrelay.readthedocs.io/",
    )
    sub = parser.add_subparsers(dest="verb", metavar="COMMAND")
    verb = sub.add_parser

    def domain(value):
        if "." in value:
            return value
        raise argparse.ArgumentTypeError(f"'{value}' is not a domain")

    init = verb(
        "init",
        help="write chatmail.ini for a domain",
        description=(
            "Write /opt/cmrelay/chatmail.ini for DOMAIN, a domain or an IPv4 address."
            " On a cmdeploy relay its chatmail.ini is copied there and DOMAIN is optional."
        ),
    )
    init.add_argument(
        "domain",
        nargs="?",
        metavar="DOMAIN",
        type=domain,
        help="the chatmail domain, or an IPv4 address",
    )

    deploy = verb(
        "deploy",
        help="make this host match its configuration",
        description=(
            "Bring this host to what /opt/cmrelay/chatmail.ini says"
            " and report what changed. A second run changes nothing."
        ),
    )
    deploy.add_argument(
        "--disable-mail",
        action="store_true",
        help="configure everything but leave postfix and dovecot stopped;"
        " the next deploy without it starts them",
    )
    deploy.add_argument(
        "--skip-dns-check",
        action="store_true",
        help="in acme mode, deploy even when the DNS records do not point here",
    )
    deploy.add_argument(
        "--verbose", "-v", action="store_true", help="name every file that changed"
    )
    deploy.add_argument("--yes", "-y", action="store_true", help="skip every prompt")

    dns = verb(
        "dns",
        help="check the published records against what this host needs",
        description=(
            "Query the authoritative nameservers for the records this host"
            " needs, and report what is missing. Exits 1 if a required record"
            " is absent."
        ),
    )
    dns.add_argument(
        "--zonefile", metavar="PATH", help="write the generated zone to PATH and exit"
    )

    status = verb(
        "status",
        help="report units, versions and conflicts",
        description=(
            "Report units, versions and conflicts, and an overall verdict."
            " Modifies nothing. Exits 0 when healthy, 1 when degraded and 2 when"
            " nothing is configured."
        ),
    )
    status.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="list every package and unit, not only their number",
    )

    upgrade = verb(
        "upgrade",
        help="install a newer signed release",
        description=(
            "Install the signed release in FILE, an installer file, and deploy it."
            " When that deploy fails, the installed release is deployed again, so"
            " all services stay on one release. To go back later, upgrade to the"
            " older installer file with --force."
        ),
    )
    upgrade.add_argument("file", metavar="FILE", help="the installer file")
    upgrade.add_argument(
        "--no-verify",
        action="store_true",
        help="skip the signature verification, for an installer you built yourself",
    )
    upgrade.add_argument(
        "--force", action="store_true", help="install a candidate that is not newer"
    )
    upgrade.add_argument(
        "--skip-dns-check",
        action="store_true",
        help="passed on to the deploy that follows",
    )
    upgrade.add_argument("--yes", "-y", action="store_true", help="skip the prompts")

    uninstall = verb(
        "uninstall",
        help="hand the host back, so `cmdeploy run` can take over",
        description=(
            "Remove /opt/cmrelay, unmask what cmrelay masked and re-enable"
            " what it disabled. Asks first."
        ),
    )
    uninstall.add_argument("--yes", "-y", action="store_true", help="do not ask")

    # Reached only from the installer's header.
    verb("installer")
    return parser


def main(argv=None):
    # Modes must not depend on the caller's umask.
    os.umask(0o022)
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.verb:
        parser.print_help()
        return 2
    out = Out(verbose=getattr(args, "verbose", False))
    try:
        bundle = Bundle.discover()
        # dns and status read the DKIM key, the certificates and acmetool's account too
        if os.geteuid() != 0:
            raise CmrelayError("cmrelay must run as root")
        if args.verb not in LOCKED:
            return VERBS[args.verb](args, bundle, out)
        with fsops.lock():
            return VERBS[args.verb](args, bundle, out)
    except CmrelayError as exc:
        out.error(str(exc))
        return 1
    except KeyboardInterrupt:
        out.error("interrupted")
        return 130
