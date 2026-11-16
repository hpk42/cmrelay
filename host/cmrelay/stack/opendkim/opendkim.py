"""opendkim: signs outgoing mail; an IPv4-only relay runs none."""

from pathlib import Path

from ... import paths
from ..postfix import postfix
from ..unbound import unbound

KEYS = Path("/etc/dkimkeys")
ETC = Path("/etc/opendkim")
CONF = paths.CONFIG / "opendkim/opendkim.conf"
SELECTOR = "opendkim"
# opendkim-genkey defaults to a weak 1024.
KEY_BITS = 2048


def configure(deploy):
    if deploy.config.ipv4_relay:
        return
    owner = ("opendkim", "opendkim")
    data = {"keys_dir": str(KEYS), "trust_anchor": str(unbound.ANCHOR)}
    data["config"] = {
        "domain_name": deploy.config.mail_domain_bare,
        "opendkim_selector": SELECTOR,
    }
    source = "stack/opendkim"
    deploy.put_template("opendkim", f"{source}/opendkim.conf", CONF, **data)
    spool = postfix.SPOOL / "opendkim"
    for directory in (ETC, KEYS, spool):
        deploy.changes.ensure_directory("opendkim", directory, mode=0o750, owner=owner)
    for name in ("KeyTable", "SigningTable"):
        dest = KEYS / name
        deploy.put_template("opendkim", f"{source}/{name}", dest, owner=owner, **data)

    private = KEYS / f"{SELECTOR}.private"
    if not private.exists():
        # openssl: opendkim-genkey would need Perl
        deploy.out.detail(f"generating a DKIM key for {deploy.config.mail_domain}")
        key = deploy.bundle.run("openssl", "genrsa", KEY_BITS).stdout
        deploy.changes.write("opendkim", private, key, mode=0o600, owner=owner)

    deploy.changes.install_dropin(
        "opendkim",
        "opendkim.service",
        "10-prevent-memory-leak.conf",
        deploy.source(f"{source}/10-prevent-memory-leak.conf").read_text(),
    )
    deploy.put_format_unit(
        "opendkim",
        f"{source}/opendkim.service",
        opendkim_bin=deploy.bin("opendkim"),
        opendkim_config=str(CONF),
    )


def units(deploy) -> list:
    return [("opendkim.service", not deploy.config.ipv4_relay, "opendkim")]
