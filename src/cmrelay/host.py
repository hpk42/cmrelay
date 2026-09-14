"""What kind of host this is, and which domain it is for.

Two tables the whole of `deploy` turns on. `partial` is a row rather than an
exception handler, so recovery is a normal code path, and the domain comes
from the ini rather than the command line, so a typo cannot rename a relay.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .errors import CmrelayError

BARE = "bare"
PARTIAL = "partial"
INSTALLED = "installed"
CMDEPLOY = "cmdeploy"
FOREIGN = "foreign"

DESCRIPTIONS = {
    BARE: "bare, nothing in the way",
    PARTIAL: "a previous run stopped part way through",
    INSTALLED: "installed by cmrelay",
    CMDEPLOY: "running chatmail, deployed by cmdeploy",
    FOREIGN: "running a mail stack cmrelay did not install",
}


class DomainRefused(CmrelayError):
    """The domain given does not match the one this host is configured for."""


@dataclass
class Host:
    state: str
    ini: Path | None = None
    relay_commit: str | None = None

    @property
    def description(self) -> str:
        return DESCRIPTIONS[self.state]

    @property
    def takes_over(self) -> bool:
        return self.state == CMDEPLOY


def read_marker(marker: Path) -> str | None:
    """The relay commit on the first line of /etc/chatmail-version."""
    if not marker.is_file():
        return None
    lines = marker.read_text().splitlines()
    return (lines[0].strip() or None) if lines else None


def classify(
    root: Path = paths.ROOT,
    ini: Path = paths.INI,
    store: Path = paths.STORE,
    cmdeploy_ini: Path = paths.CMDEPLOY_INI,
    marker: Path = paths.VERSION_MARKER,
    units: Path = paths.UNITS,
) -> Host:
    """One detector pass, no writes, before anything else happens.

    `cmdeploy` is tested before `partial`: the installer unpacks the store
    before `deploy` runs, so a host being taken over has the shape of
    `partial` and take-over would be unreachable the other way round.

    A populated store on its own is not `partial` either, since that is what
    the installer leaves behind on every fresh host. `partial` needs a trace
    of a deploy that started and did not finish: the ini, or a unit file. Not
    `state/`, which taking the deploy lock creates before this runs.
    """
    commit = read_marker(marker)
    store_populated = store.is_dir() and any(store.iterdir())

    if ini.is_file() and store_populated:
        return Host(INSTALLED, ini, commit)

    if not ini.is_file() and (cmdeploy_ini.is_file() or commit):
        return Host(CMDEPLOY, cmdeploy_ini if cmdeploy_ini.is_file() else None, commit)

    if root.exists() and (ini.is_file() or units.is_dir()):
        return Host(PARTIAL, ini if ini.is_file() else None, commit)

    return Host(BARE)


def escalate(host: Host, report) -> Host:
    """A bare host with somebody else's mail stack on it is not bare."""
    if host.state == BARE and report.conflicts:
        return Host(FOREIGN)
    return host


def config_domain(inipath: Path) -> str | None:
    """The mail_domain an ini records, without a full Config parse.

    By hand, so a malformed ini produces the config check's message.
    """
    if inipath is None or not Path(inipath).is_file():
        return None
    for line in Path(inipath).read_text(errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "mail_domain":
            return value.split("#", 1)[0].strip() or None
    return None


def resolve_domain(host: Host, given: str | None, mailboxes_dir=None):
    """Which domain this deploy is for. A given domain is checked, not obeyed."""
    recorded = config_domain(host.ini)

    if recorded is None:
        if given:
            return given
        if host.state == BARE:
            raise DomainRefused(
                "deploy needs a domain on a host with no configuration."
                "\n\n      cmrelay deploy relay.example.org"
            )
        raise DomainRefused(
            f"this host has no readable {paths.INI} and no domain was given."
            "\n  Name the domain to finish the interrupted install:"
            "\n\n      cmrelay deploy relay.example.org"
        )

    if not given or given == recorded:
        return recorded
    raise DomainRefused(mismatch_message(recorded, given, mailboxes_dir))


def mailbox_count(mailboxes_dir, cap: int = 10000) -> tuple:
    """How many maildirs there are, stopping at *cap*.

    cmrelay never walks the mailbox tree: a production relay holds hundreds
    of thousands of maildirs, and the only thing worth knowing before
    renaming a domain is the order of magnitude.
    """
    path = Path(mailboxes_dir)
    if not path.is_dir():
        return 0, False
    count = 0
    with os.scandir(path) as entries:
        for _ in entries:
            count += 1
            if count >= cap:
                return cap, True
    return count, False


def mismatch_message(recorded: str, given: str, mailboxes_dir=None) -> str:
    mailboxes_dir = mailboxes_dir or Path("/home/vmail/mail") / recorded
    lines = [
        f"this host is configured for {recorded}, not {given}.",
        "",
        "  Changing the mail domain strands the existing accounts:",
    ]
    count, capped = mailbox_count(mailboxes_dir)
    if count:
        number = f"{count} or more" if capped else str(count)
        lines.append(f"    {number} mailboxes under {mailboxes_dir}")
    lines += [
        "  and invalidates the published DKIM record.",
        "",
        "  To move this host to a new domain, uninstall and deploy again:",
        f"      cmrelay uninstall {recorded}",
        f"      cmrelay deploy {given}",
        "",
        f"  To do it in place anyway, edit mail_domain in {paths.INI}.",
    ]
    return "\n".join(lines)
