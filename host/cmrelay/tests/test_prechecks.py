"""What the prechecks find missing before a deploy."""

from types import SimpleNamespace

from conftest import fake_tool

from cmrelay.cli.deploy import check_dns_ready, check_prechecks
from cmrelay.cli.dns import dns
from cmrelay.system import prechecks
from cmrelay.system.host import Host


def test_a_missing_external_certificate_is_caught_before_anything_is_written(tmp_path):
    config = SimpleNamespace(
        tls_cert_mode="external",
        tls_cert_path=str(tmp_path / "cert.pem"),
        tls_key_path=str(tmp_path / "key.pem"),
    )
    assert len(prechecks.check_external_tls(config)) == 2

    (tmp_path / "cert.pem").write_text("x")
    (tmp_path / "key.pem").write_text("x")
    assert prechecks.check_external_tls(config) == []
    config.tls_cert_mode = "acme"
    (tmp_path / "key.pem").unlink()
    assert prechecks.check_external_tls(config) == []


def test_a_foreign_mail_stack_is_refused_and_a_cmdeploy_one_taken_over(fake_root, out):
    conflicts = prechecks.check_units()
    assert len(conflicts) == 9
    remedies = {name: remedy for _, name, _, remedy in conflicts}
    assert "mask" not in remedies["postfix.service"]
    assert "mask exim4.service" in remedies["exim4.service"]
    missing = [(prechecks.MISSING, "mtail", "", "")]
    bare, cmdeploy = Host(Host.BARE), Host(Host.CMDEPLOY)
    assert check_prechecks(bare, conflicts, out) == 1
    assert check_prechecks(Host(Host.INSTALLED), conflicts, out) == 1
    assert check_prechecks(cmdeploy, conflicts, out) is None
    assert check_prechecks(cmdeploy, conflicts + missing, out) == 1


def test_another_resolver_on_unbounds_port_is_refused(tmp_path, out):
    listed = [
        'udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=1,fd=9))',
        'udp UNCONN 0 0 127.0.0.1:53 0.0.0.0:* users:(("unbound",pid=2,fd=3))',
        'udp UNCONN 0 0 0.0.0.0:53 0.0.0.0:* users:(("dnsmasq",pid=3,fd=4))',
        'tcp LISTEN 0 32 [::]:53 [::]:* users:(("dnsmasq",pid=3,fd=6))',
    ]
    fake_tool(tmp_path, "ss", "cat <<'EOF'\n" + "\n".join(listed) + "\nEOF")
    findings = prechecks.check_port_53()
    assert [(kind, subject) for kind, subject, *_ in findings] == [
        (prechecks.MISSING, "port 53")
    ]
    assert "dnsmasq" in findings[0][2]
    assert check_prechecks(Host(Host.CMDEPLOY), findings, out) == 1


def test_a_parked_domain_is_refused_with_records(fake_root, out, capsys, monkeypatch):
    parked = {"A": "198.51.100.7", "AAAA": "", "mta-sts": "", "www": ""}
    monkeypatch.setattr(dns, "acme_answers", lambda bundle, domain: parked)
    monkeypatch.setattr(dns, "this_host", lambda: {"A": "1.2.3.4", "AAAA": None})
    config = SimpleNamespace(mail_domain="relay.example.org")
    assert not check_dns_ready(None, config, SimpleNamespace(yes=True), out)
    printed = capsys.readouterr().out
    assert "relay.example.org A is 198.51.100.7, which is not this host" in printed
    assert "IN  A      1.2.3.4" in printed and "--skip-dns-check" in printed
    assert printed.count("IN  CNAME  relay.example.org.") == 2
