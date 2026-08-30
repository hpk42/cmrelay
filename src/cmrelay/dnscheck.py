"""`cmrelay dns`, running relay's own zone code locally.

relay's `dns.py` and `remote/rdns.py` are pure functions over DNS, and on a
relay host the sshexec they were written for is just a local call. They are
vendored under `vendor/pycode/` and loaded here, with `drill` in place of dig.

Queries go to the authoritative nameserver, not the host resolver: the
relay's own unbound answers from cache, including negative answers, so an
operator who has just published a record would be told it is missing.
"""

import importlib.util
import os
import sys
from pathlib import Path

from . import config as cfg
from . import fsops, paths

PYCODE = "pycode"


class DnsError(Exception):
    pass


def load_pycode(bundle, verbose=False):
    """Load the three vendored modules under names their imports expect.

    Bytecode caching stays off: vendor/ is generated output that vendor-check
    hashes, and the installed closure is read-only anyway.
    """
    directory = bundle.vendor / PYCODE
    modules = {}
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        for name in ("rshell", "rdns", "dns"):
            path = directory / f"{name}.py"
            if not path.is_file():
                raise DnsError(f"the closure has no vendored {path.name}")
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            modules[name] = module
    finally:
        sys.dont_write_bytecode = previous
    if not verbose:
        # rshell prints a dot per query to stderr; useful over ssh, noise here.
        modules["rshell"].log_progress = lambda data: None
        modules["rdns"].log_progress = lambda data: None
    return modules["dns"], modules["rdns"]


def with_drill_on_path(bundle):
    """rdns shells out by bare name, so `drill` has to be findable."""
    drill = bundle.bin("ldns", "drill")
    os.environ["PATH"] = f"{Path(drill).parent}{os.pathsep}{os.environ.get('PATH', '')}"


def gather(bundle, config):
    """Everything the zone is built from, read off this host and the DNS."""
    dns, rdns = load_pycode(bundle)
    with_drill_on_path(bundle)
    remote_data = rdns.perform_initial_checks(config.mail_domain)
    remote_data["strict_tls"] = config.tls_cert_mode == "acme"
    return dns, rdns, remote_data


def dns_verb(args, bundle, out, config=None) -> int:
    if config is None:
        if not paths.INI.is_file():
            raise DnsError(f"{paths.INI} does not exist; this host is not installed")
        config = cfg.read_config()

    if config.ipv4_relay:
        out.print(f"{config.ipv4_relay} is not a domain; no DNS records apply.")
        return 0

    dns, rdns, remote_data = gather(bundle, config)
    if not dns.check_initial_remote_data(
        remote_data, strict_tls=remote_data["strict_tls"], print=out.warn
    ):
        return 1
    if remote_data["strict_tls"] and not remote_data.get("acme_account_url"):
        out.error("could not read the letsencrypt account url; run `cmrelay deploy`")
        return 1
    if not remote_data.get("dkim_entry"):
        out.error("could not determine the DKIM entry; run `cmrelay deploy`")
        return 1

    zonefile = dns.get_filled_zone_file(remote_data)
    if args.zonefile:
        fsops.Journal().write("dns", Path(args.zonefile), zonefile)
        out.ok(f"DNS records written to {args.zonefile}")
        return 0

    return report_zone(rdns, remote_data, zonefile, out)


def report_zone(rdns, remote_data, zonefile: str, out) -> int:
    required, recommended = rdns.check_zonefile(zonefile, verbose=False)
    if required:
        out.error("these required DNS entries are missing:")
        for line in required:
            out.print(f"  {line}")
        if remote_data.get("dkim_entry") in required:
            out.print(
                "\nIf the DKIM entry above does not work with your DNS provider, try:\n"
            )
            out.print(f"  {remote_data.get('web_dkim_entry')}")
    if recommended:
        out.warn("these recommended DNS entries are not set:")
        for line in recommended:
            out.print(f"  {line}")
    if not (required or recommended):
        out.ok("all DNS entries are published and correct")
    return 1 if required else 0


def resolves(bundle, domain: str) -> bool:
    """Whether the domain has an A or AAAA record, before anything is written."""
    _, rdns = load_pycode(bundle)
    with_drill_on_path(bundle)
    rdns.set_zone(domain)
    return bool(rdns.query_dns("A", domain) or rdns.query_dns("AAAA", domain))
