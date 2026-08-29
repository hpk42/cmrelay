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


def build_parser():
    parser = argparse.ArgumentParser(
        prog="cmrelay",
        description="Install and manage a chatmail relay from a single file.",
    )
    parser.add_argument(
        "--version", action="store_true", help="print component versions and exit"
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    install = sub.add_parser(
        "install",
        help="install a relay, on a fresh host or over an existing one",
        description=(
            "Install a chatmail relay. On a host that already carries a mail"
            " stack this refuses and modifies nothing, and says to add"
            " --take-over, which is the only way cmrelay will touch services"
            " it did not install."
        ),
    )
    install.add_argument(
        "--mail-domain",
        help="the relay's mail domain; required unless --take-over, which adopts it",
    )
    install.add_argument(
        "--take-over",
        action="store_true",
        help=(
            "convert a host that already runs chatmail: stop, disable and mask"
            " what is there, adopt its chatmail.ini, then install and start"
        ),
    )
    install.add_argument("--yes", "-y", action="store_true", help="skip the prompt")
    install.add_argument(
        "--config",
        metavar="PATH",
        help=(
            "seed /opt/cmrelay/chatmail.ini from this file instead of"
            " generating one (what cmlxc uses to inject test overrides)"
        ),
    )
    install.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="override a chatmail.ini setting (repeatable)",
    )
    install.add_argument(
        "--disable-mail",
        action="store_true",
        help="configure everything but leave postfix and dovecot stopped",
    )
    install.add_argument(
        "--force", action="store_true", help="overwrite an existing installation"
    )
    install.set_defaults(verb="install")

    status = sub.add_parser(
        "status",
        help="report units, versions and conflicts",
        description="Re-run the conflict detector and report state. Modifies nothing.",
    )
    status.set_defaults(verb="status")

    uninstall = sub.add_parser(
        "uninstall",
        help="remove cmrelay and unmask what it masked",
        description=(
            "Remove /opt/cmrelay and /nix, and unmask the distribution units"
            " --take-over masked. Roll back with `cmdeploy run` afterwards."
        ),
    )
    uninstall.add_argument("--yes", "-y", action="store_true", help="skip the prompt")
    uninstall.set_defaults(verb="uninstall")

    version = sub.add_parser("version", help="print component versions")
    version.set_defaults(verb="version")

    return parser


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
    args = parser.parse_args(argv)
    out = Out()

    if args.version and not args.command:
        args = parser.parse_args(["version"])

    if not getattr(args, "verb", None):
        parser.print_help()
        return 2

    # Both flags together would ask cmrelay to rename a live relay.
    if args.verb == "install":
        if args.take_over and (args.mail_domain or args.config or args.set):
            parser.error(
                "--take-over adopts the host's existing chatmail.ini, so"
                " --mail-domain, --config and --set do not apply"
            )
        if not args.take_over and not (args.mail_domain or args.config):
            parser.error("install needs --mail-domain (or --config, or --take-over)")

    if not installed_in_a_closure():
        from .payload import PayloadError, run_from_wheel

        try:
            return run_from_wheel(argv, out)
        except PayloadError as exc:
            out.error(str(exc))
            return 1

    from .bundle import BundleError
    from .deploy import DeployError
    from .store import StoreError
    from .users import UserError
    from .verbs import VerbError, dispatch

    try:
        return dispatch(args, out)
    except (VerbError, BundleError, DeployError, StoreError, UserError) as exc:
        out.error(str(exc))
        return 1
    except KeyboardInterrupt:
        out.error("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
