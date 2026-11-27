"""
Pure python functions which execute remotely in a system Python interpreter.

All functions of this module

- need to get and and return Python builtin data types only,

- can only use standard library dependencies,

- can freely call each other.
"""

import re

from rshell import CalledProcessError, log_progress, shell


def perform_initial_checks(mail_domain, pre_command=""):
    """Collecting initial DNS settings."""
    assert mail_domain
    set_zone(mail_domain)
    A = query_dns("A", mail_domain)
    AAAA = query_dns("AAAA", mail_domain)
    MTA_STS = query_dns("CNAME", f"mta-sts.{mail_domain}")
    WWW = query_dns("CNAME", f"www.{mail_domain}")

    res = dict(mail_domain=mail_domain, A=A, AAAA=AAAA, MTA_STS=MTA_STS, WWW=WWW)
    res["ADSP"] = query_dns("TXT", f"_adsp._domainkey.{mail_domain}")
    res["acme_account_url"] = shell(
        pre_command + "acmetool account-url", fail_ok=True, print=log_progress
    )
    res["dkim_entry"], res["web_dkim_entry"] = get_dkim_entry(
        mail_domain, pre_command, dkim_selector="opendkim"
    )

    if not MTA_STS or not WWW or (not A and not AAAA):
        return res

    # parse out sts-id if exists, example: "v=STSv1; id=2090123"
    mta_sts_txt = query_dns("TXT", f"_mta-sts.{mail_domain}")
    if not mta_sts_txt:
        return res
    parts = mta_sts_txt.split("id=")
    res["sts_id"] = parts[1].rstrip('"') if len(parts) == 2 else ""
    return res


def get_dkim_entry(mail_domain, pre_command, dkim_selector):
    try:
        dkim_pubkey = shell(
            f"{pre_command}openssl rsa -in /etc/dkimkeys/{dkim_selector}.private "
            "-pubout 2>/dev/null | awk '/-/{next}{printf(\"%s\",$0)}'",
            print=log_progress,
        )
    except CalledProcessError:
        return None, None
    dkim_value_raw = f"v=DKIM1;k=rsa;p={dkim_pubkey};s=email;t=s"
    dkim_value = '" "'.join(re.findall(".{1,255}", dkim_value_raw))
    web_dkim_value = "".join(re.findall(".{1,255}", dkim_value_raw))
    name = f"{dkim_selector}._domainkey.{mail_domain}."
    return (
        f'{name:<40} 3600   IN  TXT    "{dkim_value}"',
        f'{name:<40} 3600   IN  TXT    "{web_dkim_value}"',
    )


# The zone under test has one set of authoritative nameservers, resolved
# once from the mail domain. Asking per record would follow a CNAME and take
# its target for a nameserver: `drill -Q` prints the whole answer section.
NAMESERVER = []


def get_authoritative_ns(domain):
    """Walk up the labels until a name server answers, stopping at two."""
    labels = domain.rstrip(".").split(".")
    while len(labels) >= 2:
        name = ".".join(labels)
        answer = shell(f"drill -Q {name} NS", fail_ok=True, print=log_progress)
        for line in answer.split("\n"):
            line = line.strip().rstrip(".")
            if line and not line.startswith(";"):
                return line
        labels = labels[1:]
    return None


def set_zone(mail_domain):
    NAMESERVER[:] = [ns for ns in [get_authoritative_ns(mail_domain)] if ns]
    return NAMESERVER[0] if NAMESERVER else None


def query_dns(typ, domain, ns=None):
    if ns is None:
        ns = NAMESERVER[0] if NAMESERVER else get_authoritative_ns(domain)

    # Query the authoritative nameserver directly to bypass DNS cache.
    direct_ns = f"@{ns}" if ns else ""
    res = shell(
        f"drill -Q {direct_ns} {domain} {typ}", fail_ok=True, print=log_progress
    )
    return next((line for line in res.split("\n") if not line.startswith(";")), "")


def check_zonefile(zonefile, verbose=True):
    """Check expected zone file entries."""
    required = True
    required_diff = []
    recommended_diff = []

    for zf_line in zonefile.splitlines():
        if "; Recommended" in zf_line:
            required = False
            continue
        if not zf_line.strip() or zf_line.startswith(";"):
            continue
        print(f"dns-checking {zf_line!r}") if verbose else log_progress("")
        zf_domain, _ttl, _in, zf_typ, zf_value = zf_line.split(None, 4)
        zf_domain = zf_domain.rstrip(".")
        zf_value = zf_value.strip()
        query_value = query_dns(zf_typ, zf_domain)
        if zf_value != query_value:
            assert zf_typ in ("A", "AAAA", "CNAME", "CAA", "SRV", "MX", "TXT"), zf_line
            if required:
                required_diff.append(zf_line)
            else:
                recommended_diff.append(zf_line)

    return required_diff, recommended_diff
