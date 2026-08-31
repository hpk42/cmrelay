"""The vendored zone code, and the drill output it parses.

The recorded output pins the dig-to-drill swap: `drill -Q` was verified
byte-identical to `dig +short` for every record type the zone check compares,
and these fixtures are what would notice that stopping being true.
"""

import io
import stat
from types import SimpleNamespace

import pytest
from conftest import REPO  # noqa: I001 - added to sys.path by conftest

from cmrelay import dnscheck
from cmrelay.output import Out

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


def fake_drill(tmp_path):
    """A drill that answers from RECORDED and nothing else."""
    lines = [
        "#!/bin/sh",
        "shift 1",
        'case "$1" in "@"*) shift ;; esac',
        'case "$1 $2" in',
    ]
    for (rtype, name), answer in RECORDED.items():
        payload = answer.replace("\n", "\\n").rstrip("\\n")
        lines.append(f"  '{name} {rtype}') printf '{payload}\\n' ;;")
    lines += ["  *) : ;;", "esac", "exit 0"]
    path = tmp_path / "drill"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def rdns(tmp_path, monkeypatch):
    bundle = SimpleNamespace(
        vendor=REPO / "vendor",
        bin=lambda component, name=None: str(fake_drill(tmp_path)),
    )
    _, module = dnscheck.load_pycode(bundle)
    dnscheck.with_drill_on_path(bundle)
    return module


@pytest.fixture
def dns(tmp_path):
    bundle = SimpleNamespace(vendor=REPO / "vendor")
    module, _ = dnscheck.load_pycode(bundle)
    return module


def test_the_vendored_modules_import_without_a_package(dns, rdns):
    assert hasattr(dns, "get_filled_zone_file")
    assert hasattr(rdns, "check_zonefile")


def test_the_authoritative_ns_walk_drops_labels_until_one_answers(rdns):
    assert rdns.get_authoritative_ns("mta-sts.relay.example.org") == "ns1.example.net"


def test_the_walk_stops_rather_than_reaching_the_root(rdns):
    assert rdns.get_authoritative_ns("nothing.invalid") is None


@pytest.mark.parametrize(
    ("rtype", "name", "expected"),
    [
        ("A", "relay.example.org", "203.0.113.10"),
        ("AAAA", "relay.example.org", "2001:db8::10"),
        ("CNAME", "www.relay.example.org", "relay.example.org."),
        ("MX", "relay.example.org", "10 relay.example.org."),
        ("TXT", "relay.example.org", '"v=spf1 a ~all"'),
        ("SRV", "_imaps._tcp.relay.example.org", "0 1 993 relay.example.org."),
    ],
)
def test_query_dns_returns_the_bare_rdata(rdns, rtype, name, expected):
    assert rdns.query_dns(rtype, name) == expected


def test_a_name_with_no_record_is_an_empty_answer(rdns):
    assert rdns.query_dns("A", "absent.relay.example.org") == ""


def remote_data(**overrides):
    data = {
        "mail_domain": "relay.example.org",
        "A": "203.0.113.10",
        "AAAA": "2001:db8::10",
        "MTA_STS": "relay.example.org.",
        "WWW": "relay.example.org.",
        "sts_id": "202608301200",
        "strict_tls": True,
        "acme_account_url": "https://acme/acct/1",
        "dkim_entry": (
            'opendkim._domainkey.relay.example.org.  3600   IN  TXT    "v=DKIM1"'
        ),
        "web_dkim_entry": "opendkim._domainkey.relay.example.org. 3600 IN TXT v=DKIM1",
    }
    data.update(overrides)
    return data


def test_the_generated_zone_carries_every_required_record(dns):
    zone = dns.get_filled_zone_file(remote_data())
    lines = {" ".join(line.split()) for line in zone.splitlines()}
    for needle in (
        "relay.example.org. 3600 IN A 203.0.113.10",
        "relay.example.org. 3600 IN AAAA 2001:db8::10",
        "relay.example.org. 3600 IN MX 10 relay.example.org.",
        "mta-sts.relay.example.org. 3600 IN CNAME relay.example.org.",
    ):
        assert needle in lines, needle
    assert 'IN TXT "v=STSv1; id=202608301200"' in " ".join(zone.split())
    assert "www.relay.example.org." in zone
    assert "v=DKIM1" in zone


def test_the_recommended_half_is_marked(dns):
    zone = dns.get_filled_zone_file(remote_data())
    required, recommended = zone.split("; Recommended DNS entries")
    assert "v=spf1" not in required
    assert "v=spf1" in recommended
    assert "letsencrypt.org" in recommended


def test_a_published_zone_reports_nothing_missing(dns, rdns):
    zone = dns.get_filled_zone_file(remote_data(dkim_entry="", web_dkim_entry=""))
    required, recommended = rdns.check_zonefile(zone, verbose=False)
    assert required == []
    assert [line for line in recommended if "_dmarc" not in line and "SRV" not in line]


def test_a_missing_required_record_is_reported(dns, rdns):
    zone = dns.get_filled_zone_file(
        remote_data(A="203.0.113.99", dkim_entry="", web_dkim_entry="")
    )
    required, _ = rdns.check_zonefile(zone, verbose=False)
    assert any("203.0.113.99" in line for line in required)


def test_an_ipv4_relay_has_no_records_to_publish():
    stream = io.StringIO()
    out = Out(stream=stream, colour=False)
    config = SimpleNamespace(ipv4_relay="203.0.113.10")
    assert dnscheck.dns_verb(SimpleNamespace(zonefile=None), None, out, config) == 0
    assert "no DNS records apply" in stream.getvalue()


def test_the_zone_nameserver_is_resolved_once_and_reused(rdns):
    """Asking per record follows a CNAME and takes its target for a nameserver."""
    assert rdns.set_zone("relay.example.org") == "ns1.example.net"
    assert rdns.NAMESERVER == ["ns1.example.net"]
    assert rdns.query_dns("CNAME", "www.relay.example.org") == "relay.example.org."


def test_a_resolver_that_fails_yields_an_empty_answer_not_a_traceback(rdns):
    rdns.NAMESERVER[:] = []
    assert rdns.query_dns("A", "nothing.invalid") == ""
