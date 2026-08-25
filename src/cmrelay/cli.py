"""The cmrelay command line.

Four verbs and a version. `install --mail-domain <domain>` is the primary
path and takes exactly one required argument, because everything else has a
default that relay's own write_initial_config supplies.

The same CLI runs from two places, and the difference decides what it can do.
Out of the closure -- which is how the .run payload starts it -- everything
works. Out of a pip-installed wheel there is no closure, no chatmaild and no
relay to install, so the verbs that need one download the payload and hand
over to it. That is why the verb implementations are imported inside
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
        help="install onto a fresh host",
        description=(
            "Install a chatmail relay. Refuses and modifies nothing if the host"
            " already carries a mail stack; use `upgrade` for that."
        ),
    )
    install.add_argument("--mail-domain", required=True, help="the relay's mail domain")
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
    install.set_defaults(verb="install", needs_closure=True)

    upgrade = sub.add_parser(
        "upgrade",
        help="convert an existing deployment to cmrelay",
        description=(
            "Convert a host to cmrelay. This is the only command permitted to"
            " stop, disable and mask existing services."
        ),
    )
    upgrade.add_argument("--yes", "-y", action="store_true", help="skip the prompt")
    upgrade.add_argument(
        "--disable-mail",
        action="store_true",
        help="configure everything but leave postfix and dovecot stopped",
    )
    upgrade.set_defaults(verb="upgrade", needs_closure=True)

    status = sub.add_parser(
        "status",
        help="report units, versions and conflicts",
        description="Re-run the conflict detector and report state. Modifies nothing.",
    )
    status.set_defaults(verb="status", needs_closure=True)

    uninstall = sub.add_parser(
        "uninstall",
        help="remove cmrelay and unmask what it masked",
        description=(
            "Remove /opt/cmrelay and /nix, and unmask the distribution units"
            " upgrade masked. Roll back with `cmdeploy run` afterwards."
        ),
    )
    uninstall.add_argument("--yes", "-y", action="store_true", help="skip the prompt")
    uninstall.set_defaults(verb="uninstall", needs_closure=True)

    version = sub.add_parser("version", help="print component versions")
    version.set_defaults(verb="version", needs_closure=True)

    download = sub.add_parser(
        "download",
        help="fetch the payload for this version without installing it",
        description=(
            "Download the release payload matching this cmrelay's version and"
            " print where it landed. Useful for staging an install on a host"
            " with no outbound access."
        ),
    )
    download.set_defaults(verb="download", needs_closure=False)

    return parser


def installed_in_a_closure() -> bool:
    """Whether this CLI is the one inside a cmrelay payload."""
    from .bundle import Bundle, BundleError

    try:
        Bundle.discover()
    except BundleError:
        return False
    return True


def main(argv=None):
    # The modes of everything cmrelay creates must not depend on who invoked
    # it. makeself's stub runs with `umask 077`, which silently made
    # /var/lib/postfix unreadable to the postfix user and stopped the mail
    # system from starting -- the sort of failure that only shows up on the
    # path nobody runs by hand.
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

    # A pip-installed cmrelay carries the CLI and nothing else. Every verb
    # that touches a relay needs the closure, so fetch it and hand over.
    if args.needs_closure and not installed_in_a_closure():
        from .fetch import FetchError, run_from_wheel

        try:
            return run_from_wheel(argv, out)
        except FetchError as exc:
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
