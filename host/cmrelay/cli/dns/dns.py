"""`cmrelay dns`, running cmdeploy's zone code with `drill`.

Queries go to the authoritative nameserver: unbound would answer from its cache.
"""

import importlib.util
import ipaddress
import os
import socket
import sys
from pathlib import Path

from ... import paths
from ...stack import config as cfg
from ...system import fsops


def load_pycode(bundle):
    """Load the three copied modules under the names their imports expect, tools first on PATH."""
    modules = {}
    for name in ("rshell", "rdns", "zone"):
        path = bundle.package / f"cli/dns/{name}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        modules[name] = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modules[name])
    # rdns prints a dot per query to stderr; useful over ssh, noise here.
    modules["rdns"].log_progress = lambda data: None
    tools = ("drill", "acmetool", "openssl")
    dirs = [str(Path(bundle.tool(name)).parent) for name in tools]
    os.environ["PATH"] = os.pathsep.join([*dirs, os.environ.get("PATH", "")])
    return modules["zone"], modules["rdns"]


def dns_verb(args, bundle, out) -> int:
    config = cfg.read_config(paths.INI)

    if config.ipv4_relay:
        out.print(f"{config.ipv4_relay} is not a domain; no DNS records apply.")
        return 0

    dns, rdns = load_pycode(bundle)
    remote_data = rdns.perform_initial_checks(config.mail_domain)
    remote_data["strict_tls"] = config.tls_cert_mode == "acme"
    if not dns.check_initial_remote_data(
        remote_data, strict_tls=remote_data["strict_tls"], print=out.error
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
        fsops.Changes().write("dns", Path(args.zonefile), zonefile)
        out.ok(f"DNS records written to {args.zonefile}")
        return 0

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


def acme_answers(bundle, domain: str) -> dict:
    """The authoritative answers for every name the certificate covers."""
    _, rdns = load_pycode(bundle)
    rdns.set_zone(domain)
    answers = {typ: rdns.query_dns(typ, domain) for typ in ("A", "AAAA")}
    for sub in ("mta-sts", "www"):
        answers[sub] = rdns.query_dns("CNAME", f"{sub}.{domain}")
    return answers


def zone_line(name, typ, value) -> str:
    """One record in the layout of chatmail/relay's zone file."""
    return f"{name + '.':<40} 3600   IN  {typ:<5}  {value}"


def sending_address(family, probe: str) -> str | None:
    """This host's source address towards *probe*; nothing is sent."""
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.connect((probe, 53))
            return sock.getsockname()[0]
    except OSError:
        return None


def this_host() -> dict:
    return {
        "A": sending_address(socket.AF_INET, "9.9.9.9"),
        "AAAA": sending_address(socket.AF_INET6, "2620:fe::fe"),
    }


def owns(address: str) -> bool:
    """Whether *address* is configured here: only then can it bind."""
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.bind((address, 0))
            return True
    except OSError:
        return False


def acme_readiness(domain, answers, mine, owns=owns) -> tuple:
    """What stops acmetool from validating *domain*, and the records that fix it.

    A domain parked at its registrar resolves, but the parking page fails
    acmetool's self-test, so the address records must point here.
    *mine* holds this host's address per record type; a private one means NAT.
    """
    problems, records = [], []
    public = {t: a for t, a in mine.items() if a and ipaddress.ip_address(a).is_global}
    if not answers["A"] and not answers["AAAA"]:
        problems.append(f"{domain} has no A or AAAA record")
        address = public.get("A", "<this host's public IPv4>")
        records.append(zone_line(domain, "A", address))
    for typ, family in (("A", "IPv4"), ("AAAA", "IPv6")):
        answer = answers[typ]
        if not answer or owns(answer):
            continue
        if mine[typ] is None:
            problems.append(f"delete {typ} {answer}, this host has no {family}")
        elif typ in public:
            problems.append(f"{domain} {typ} is {answer}, which is not this host")
            records.append(zone_line(domain, typ, public[typ]))
    for sub in ("mta-sts", "www"):
        if answers[sub] != f"{domain}.":
            problems.append(f"{sub}.{domain} is no CNAME for {domain}")
            records.append(zone_line(f"{sub}.{domain}", "CNAME", f"{domain}."))
    return problems, records
