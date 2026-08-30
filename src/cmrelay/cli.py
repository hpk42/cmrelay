"""The cmrelay command line.

The same CLI runs out of the closure, where everything works, and out of a
pip-installed wheel, where there is no chatmaild and every verb hands over to
the bundled payload. That is why the verb implementations are imported inside
`main()`: importing them from a wheel would fail on chatmaild before the CLI
could explain itself.
"""

import argparse
import os
import sys

from .output import Out


def verb(sub, name, **kwargs):
    """A subparser carrying the output flags, so they work after the verb too."""
    return sub.add_parser(name, parents=[sub._common], **kwargs)


def output_flags(parser, after_the_verb=False):
    """Accepted before the verb and after it, because both get typed.

    The post-verb copy suppresses its defaults, so `cmrelay --verbose deploy`
    is not undone by the subparser setting verbose back to False.
    """
    unset = argparse.SUPPRESS if after_the_verb else None
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default=unset or "auto",
        help="colourise the output (default: auto, and NO_COLOR is honoured)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=unset or False,
        help="name every file that moved",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        default=unset or False,
        help="print only warnings, errors and the summary",
    )
    return parser


def build_parser():
    parser = argparse.ArgumentParser(
        prog="cmrelay",
        description="Install and manage a chatmail relay from a single file.",
    )
    parser.add_argument(
        "--version", action="store_true", help="print component versions and exit"
    )
    output_flags(parser)

    common = argparse.ArgumentParser(add_help=False)
    output_flags(common, after_the_verb=True)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub._common = common

    add_deploy(sub)
    add_dns(sub)
    add_status(sub)
    add_upgrade(sub)
    add_uninstall(sub)
    add_installer(sub)

    version = verb(sub, "version", help="print component versions")
    version.set_defaults(verb="version")
    return parser


def add_deploy(sub):
    deploy = verb(
        sub,
        "deploy",
        help="make this host match its configuration",
        description=(
            "Bring this host to what chatmail.ini says, and report what moved."
            " Idempotent: a second run changes nothing. DOMAIN is needed only"
            " on a host that has no chatmail.ini yet."
        ),
    )
    deploy.add_argument(
        "domain", nargs="?", metavar="DOMAIN", help="the chatmail domain, or an IPv4"
    )
    deploy.add_argument(
        "--take-over",
        action="store_true",
        help="adopt a host already running chatmail, without further prompting",
    )
    deploy.add_argument(
        "--force",
        action="store_true",
        help="deploy under a domain other than the one this host is configured for",
    )
    deploy.add_argument("--yes", "-y", action="store_true", help="skip every prompt")
    deploy.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="override a chatmail.ini setting (repeatable)",
    )
    deploy.add_argument(
        "--config",
        metavar="PATH",
        help="seed chatmail.ini from this file instead of generating one",
    )
    deploy.add_argument(
        "--website-only", action="store_true", help="only rebuild the web pages"
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
    deploy.add_argument(
        "--reconcile",
        action="store_true",
        help="run acmetool even when the certificate is still good",
    )
    deploy.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change, change nothing",
    )
    deploy.set_defaults(verb="deploy")


def add_dns(sub):
    dns = verb(
        sub,
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
    dns.set_defaults(verb="dns")


def add_status(sub):
    status = verb(
        sub,
        "status",
        help="report units, versions and conflicts",
        description=(
            "Re-run the conflict detector and report state. Modifies nothing."
            " Exits 0 healthy, 1 degraded, 2 not installed."
        ),
    )
    status.add_argument(
        "--json", action="store_true", help="emit the same facts as one JSON object"
    )
    status.set_defaults(verb="status")


def add_upgrade(sub):
    upgrade = verb(
        sub,
        "upgrade",
        help="install a newer signed release",
        description=(
            "Stage a newer release beside the running one, run its migrations,"
            " flip the generation symlink and deploy. The previous generation"
            " stays on disk for --rollback."
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
        "--auto", action="store_true", help="look for a newer release without asking"
    )
    upgrade.add_argument(
        "--source",
        choices=("github", "pypi"),
        help="only ask this source, rather than both",
    )
    upgrade.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="accept a candidate carrying no verifiable signature",
    )
    upgrade.add_argument(
        "--force", action="store_true", help="install a candidate that is not newer"
    )
    upgrade.add_argument("--yes", "-y", action="store_true", help="skip the prompt")
    # The half that runs from the new closure, so its migrations are the new
    # release's own rather than the one being replaced.
    upgrade.add_argument("--finish", help=argparse.SUPPRESS)
    upgrade.add_argument("--previous-version", help=argparse.SUPPRESS)
    upgrade.set_defaults(verb="upgrade")


def add_uninstall(sub):
    uninstall = verb(
        sub,
        "uninstall",
        help="hand the host back, so `cmdeploy run` can take over",
        description=(
            "Remove /opt/cmrelay and /nix, and unmask the distribution units"
            " take-over masked. Name the domain to confirm."
        ),
    )
    uninstall.add_argument(
        "domain", nargs="?", metavar="DOMAIN", help="the domain this relay serves"
    )
    uninstall.add_argument(
        "--yes", "-y", action="store_true", help="skip the confirmation"
    )
    uninstall.set_defaults(verb="uninstall")


def add_installer(sub):
    """Reached only from the installer stub, which passes its own argv on."""
    installer = verb(sub, "installer")
    installer.set_defaults(verb="installer", yes=False, stage_only=False, rest=[])


def split_installer(argv):
    """The installer's own flags, and the argv `cmrelay deploy` is to get.

    Parsed by hand rather than with REMAINDER, which errors on an unknown
    option before the first positional -- and every deploy flag is one.
    """
    yes = any(a in ("-y", "--yes") for a in argv)
    stage_only = "--stage-only" in argv
    rest = [a for a in argv if a not in ("-y", "--yes", "--stage-only", "--")]
    return yes, stage_only, rest


def installed_in_a_closure() -> bool:
    from .bundle import Bundle, BundleError

    try:
        Bundle.discover()
    except BundleError:
        return False
    return True


def main(argv=None):
    # Modes must not depend on who invoked cmrelay: makeself's `umask 077`
    # silently made /var/lib/postfix unreadable to the postfix user.
    os.umask(0o022)

    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    passed_on = None
    if argv and argv[0] == "installer":
        passed_on = argv[1:]
        argv = argv[:1]

    args = parser.parse_args(argv)
    if passed_on is not None:
        args.yes, args.stage_only, args.rest = split_installer(passed_on)
    out = Out(colour=args.color, verbose=args.verbose, quiet=args.quiet)

    if args.version and not args.command:
        args = parser.parse_args(["version"])

    if not getattr(args, "verb", None):
        parser.print_help()
        return 2

    if args.verb == "deploy" and args.take_over and (args.config or args.set):
        parser.error(
            "--take-over adopts the host's existing chatmail.ini, so --config"
            " and --set do not apply"
        )

    if not installed_in_a_closure():
        from .payload import PayloadError, run_from_wheel

        try:
            return run_from_wheel(argv, out)
        except PayloadError as exc:
            out.error(str(exc))
            return 1

    from .bundle import BundleError
    from .config import ConfigError
    from .deploy import DeployError
    from .dnscheck import DnsError
    from .fsops import LockBusy
    from .host import DomainRefused
    from .installer import InstallerError
    from .preflight import PreflightError
    from .signing import SigningError
    from .store import StoreError
    from .systemd import SystemdError
    from .upgrade import UpgradeError
    from .users import UserError
    from .verbs import VerbError, dispatch
    from .website import WebsiteError

    try:
        return dispatch(args, out)
    except (
        VerbError,
        BundleError,
        ConfigError,
        DeployError,
        DnsError,
        DomainRefused,
        InstallerError,
        LockBusy,
        PreflightError,
        SigningError,
        StoreError,
        SystemdError,
        UpgradeError,
        UserError,
        WebsiteError,
    ) as exc:
        out.error(str(exc))
        return 1
    except KeyboardInterrupt:
        out.error("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
