"""The cmrelay command line."""

import argparse
import os
import sys

from .errors import CmrelayError
from .output import Out
from .verbs import VERBS


def build_parser():
    parser = argparse.ArgumentParser(
        prog="cmrelay", description="Install and maintain a chatmail relay."
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--verbose", "-v", action="store_true", help="name every file that moved"
    )
    sub = parser.add_subparsers(dest="verb", metavar="COMMAND")

    def verb(name, **kwargs):
        return sub.add_parser(name, parents=[common], **kwargs)

    def domain(value):
        if "." in value:
            return value
        hint = f"; did you mean `cmrelay {value}`?" if value in sub.choices else ""
        raise argparse.ArgumentTypeError(f"'{value}' is not a domain{hint}")

    domain_arg = dict(nargs="?", metavar="DOMAIN", type=domain)

    deploy = verb(
        "deploy",
        help="make this host match its configuration",
        description=(
            "Bring this host to what chatmail.ini says, and report what moved."
            " Idempotent: a second run changes nothing. DOMAIN is needed only"
            " on a host that has no chatmail.ini yet."
        ),
    )
    deploy.add_argument("domain", **domain_arg, help="the chatmail domain, or an IPv4")
    deploy.add_argument(
        "--config",
        metavar="PATH",
        help="seed chatmail.ini from this file instead of generating one",
    )
    mail = deploy.add_mutually_exclusive_group()
    mail.add_argument(
        "--disable-mail",
        dest="enable_mail",
        action="store_false",
        default=None,
        help="configure everything but leave postfix and dovecot stopped",
    )
    mail.add_argument(
        "--enable-mail",
        dest="enable_mail",
        action="store_true",
        default=None,
        help="undo an earlier --disable-mail",
    )
    deploy.add_argument(
        "--skip-dns-check",
        action="store_true",
        help="do not resolve the domain before writing anything",
    )
    deploy.add_argument("--yes", "-y", action="store_true", help="skip every prompt")

    dns = verb(
        "dns",
        help="check the published records against what this relay needs",
        description=(
            "Query the authoritative nameservers for the records this relay"
            " needs, and report what is missing. Exits 1 if a required record"
            " is absent."
        ),
    )
    dns.add_argument(
        "--zonefile", metavar="PATH", help="write the generated zone here and exit"
    )

    verb(
        "status",
        help="report units, versions and conflicts",
        description=(
            "Re-run the conflict detector and report state. Modifies nothing."
            " Exits 0 healthy, 1 degraded, 2 not installed."
        ),
    )

    upgrade = verb(
        "upgrade",
        help="install a newer signed release",
        description=(
            "Stage a newer release beside the running one, flip the"
            " generation symlink and deploy. The previous generation stays"
            " on disk for --rollback."
        ),
    )
    upgrade.add_argument(
        "file", nargs="?", metavar="FILE", help="an installer to upgrade to"
    )
    upgrade.add_argument(
        "--check", action="store_true", help="say what would happen and stop"
    )
    upgrade.add_argument(
        "--rollback", action="store_true", help="go back to the previous generation"
    )
    upgrade.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="accept a candidate carrying no verifiable signature",
    )
    upgrade.add_argument(
        "--force", action="store_true", help="install a candidate that is not newer"
    )
    upgrade.add_argument("--yes", "-y", action="store_true", help="skip the prompts")

    uninstall = verb(
        "uninstall",
        help="hand the host back, so `cmdeploy run` can take over",
        description=(
            "Remove /opt/cmrelay and /nix, and unmask the distribution units"
            " take-over masked. Name the domain to confirm."
        ),
    )
    uninstall.add_argument("domain", **domain_arg, help="the domain this relay serves")
    uninstall.add_argument(
        "--yes", "-y", action="store_true", help="skip the confirmation"
    )

    verb("version", help="print component versions")

    # Reached only from the installer stub, which passes its own flags on.
    installer = verb("installer")
    installer.add_argument("--yes", "-y", action="store_true")
    installer.add_argument("--stage-only", action="store_true")
    return parser


def main(argv=None):
    # Modes must not depend on who invoked cmrelay: an installer run under
    # `umask 077` once made /var/lib/postfix unreadable to the postfix user.
    os.umask(0o022)
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.verb:
        parser.print_help()
        return 2
    out = Out(verbose=args.verbose)
    try:
        return VERBS[args.verb](args, out)
    except CmrelayError as exc:
        out.error(str(exc))
        return 1
    except KeyboardInterrupt:
        out.error("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
