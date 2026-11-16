"""The copied zone code, and the drill output it parses.

The fixtures pin that `drill -Q` prints what `dig +short` prints.
"""

from types import SimpleNamespace

import pytest
from conftest import REPO, fake_tool

from cmrelay.cli.dns import dns

# One authoritative answer per record type the generated zone carries.
RECORDED = {
    ("NS", "example.org"): "ns1.example.net.\nns2.example.net.\n",
    ("A", "relay.example.org"): "203.0.113.10\n",
    ("AAAA", "relay.example.org"): "2001:db8::10\n",
    ("CNAME", "mta-sts.relay.example.org"): "relay.example.org.\n",
    ("CNAME", "www.relay.example.org"): "relay.example.org.\n",
    ("MX", "relay.example.org"): "10 relay.example.org.\n",
    ("TXT", "relay.example.org"): '"v=spf1 a ~all"\n',
    ("TXT", "_mta-sts.relay.example.org"): '"v=STSv1; id=202608301200"\n',
    ("SRV", "_imaps._tcp.relay.example.org"): "0 1 993 relay.example.org.\n",
    (
        "CAA",
        "relay.example.org",
    ): '0 issue "letsencrypt.org;accounturi=https://acme/acct/1"\n',
}


@pytest.fixture
def answering(tmp_path):
    """A closure whose drill answers from RECORDED."""
    lines = ["shift 1", 'case "$1" in "@"*) shift ;; esac', 'case "$1 $2" in']
    for (rtype, name), answer in RECORDED.items():
        payload = answer.replace("\n", "\\n").rstrip("\\n")
        lines.append(f"  '{name} {rtype}') printf '{payload}\\n' ;;")
    drill = fake_tool(tmp_path, "drill", "\n".join([*lines, "esac"]))
    return SimpleNamespace(package=REPO / "host/cmrelay", tool=lambda name: drill)


def test_drill_answers_are_read_like_dig_short(answering):
    _, rdns = dns.load_pycode(answering)
    assert rdns.get_authoritative_ns("mta-sts.relay.example.org") == "ns1.example.net"
    assert rdns.get_authoritative_ns("nothing.invalid") is None
    # asking per record would follow a CNAME and take its target for a nameserver
    assert rdns.set_zone("relay.example.org") == "ns1.example.net"
    for (rtype, name), answer in RECORDED.items():
        if rtype != "NS":
            assert rdns.query_dns(rtype, name) == answer.strip()
    assert rdns.query_dns("A", "absent.relay.example.org") == ""
    rdns.NAMESERVER[:] = []
    assert rdns.query_dns("A", "nothing.invalid") == ""


def test_the_generated_zone_is_checked_against_what_is_published(answering):
    zone, rdns = dns.load_pycode(answering)
    data = {
        "mail_domain": "relay.example.org",
        "A": "203.0.113.10",
        "AAAA": "2001:db8::10",
        "MTA_STS": "relay.example.org.",
        "WWW": "relay.example.org.",
        "sts_id": "202608301200",
        "strict_tls": True,
        "acme_account_url": "https://acme/acct/1",
        "dkim_entry": "",
        "web_dkim_entry": "",
    }
    required, _ = rdns.check_zonefile(zone.get_filled_zone_file(data), verbose=False)
    assert required == []
    data["A"] = "203.0.113.99"
    required, _ = rdns.check_zonefile(zone.get_filled_zone_file(data), verbose=False)
    assert any("203.0.113.99" in line for line in required)


def test_acme_answers_are_the_names_the_certificate_covers(answering):
    assert dns.acme_answers(answering, "relay.example.org") == {
        "A": "203.0.113.10",
        "AAAA": "2001:db8::10",
        "mta-sts": "relay.example.org.",
        "www": "relay.example.org.",
    }


# A host on the internet by IPv4, with no IPv6 route. Documentation addresses
# are not global, so they would read as NAT.
HERE = {"A": "1.2.3.4", "AAAA": None}


def readiness(mine=HERE, **overrides):
    answers = {"A": "1.2.3.4", "AAAA": "", "mta-sts": "relay.example.org."}
    answers["www"] = "relay.example.org."
    answers.update(overrides)
    problems, records = dns.acme_readiness(
        "relay.example.org", answers, mine, owns=lambda a: a in mine.values()
    )
    return problems, [" ".join(line.split()) for line in records]


def test_a_domain_pointing_here_with_both_cnames_is_ready():
    assert readiness() == ([], [])


def test_no_address_record_at_all_names_this_hosts_address():
    _, records = readiness(A="")
    assert records == ["relay.example.org. 3600 IN A 1.2.3.4"]


def test_an_aaaa_record_on_a_host_without_ipv6_is_to_be_deleted():
    problems, records = readiness(AAAA="2001:db8::99")
    assert records == []
    assert "no IPv6" in problems[0] and "delete" in problems[0]


def test_behind_nat_the_address_records_cannot_be_judged():
    nat = {"A": "10.0.0.5", "AAAA": None}
    assert readiness(mine=nat, A="198.51.100.7") == ([], [])
    _, records = readiness(mine=nat, A="")
    assert records == ["relay.example.org. 3600 IN A <this host's public IPv4>"]
