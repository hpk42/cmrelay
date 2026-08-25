"""A failed preflight must leave the host untouched, so it is worth testing.

These exercise the parsing and the decisions, not the host: `ss` and
`systemctl` are replaced by real scripts on PATH rather than mocks, so the
code under test runs the same subprocess path it runs on a relay.
"""

import json
import os
import stat

import pytest
from conftest import REPO  # noqa: I001 - added to sys.path by conftest

from cmrelay import preflight


def fake_tool(tmp_path, name, script) -> str:
    """A real executable that prints canned output, standing in for a daemon."""
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class Config:
    """The handful of attributes relay's port table dereferences."""

    tls_cert_mode = "acme"
    filtermail_smtp_port = 10080
    filtermail_smtp_port_incoming = 10081
    filtermail_http_port_incoming = 10082
    filtermail_lmtp_port_transport = 10083
    postfix_reinject_port = 10025
    postfix_reinject_port_incoming = 10026


@pytest.fixture
def port_table():
    return json.loads((REPO / "nix/generated/relay-pins.json").read_text())[
        "port_table"
    ]


def test_the_real_table_resolves_completely(port_table):
    resolved = preflight.resolve_port_table(port_table, Config())
    ports = {port for _, port in resolved}
    assert {25, 53, 80, 143, 443, 465, 587, 993, 3340, 3903, 3904, 8443} <= ports
    assert 402 in ports  # acme mode
    assert {10080, 10081, 10082, 10083, 10025, 10026} <= ports
    assert all(isinstance(port, int) for _, port in resolved)


def test_the_acme_only_entry_drops_out_in_self_signed_mode(port_table):
    config = Config()
    config.tls_cert_mode = "self"
    ports = {port for _, port in preflight.resolve_port_table(port_table, config)}
    assert 402 not in ports


def test_a_config_attribute_relay_added_and_we_lack_is_an_error(port_table):
    class Sparse(Config):
        filtermail_smtp_port = None

    with pytest.raises(ValueError, match="filtermail_smtp_port"):
        preflight.resolve_port_table(port_table, Sparse())


def test_a_free_port_produces_no_finding(tmp_path):
    ss = fake_tool(tmp_path, "ss", "exit 0")
    report = preflight.check_ports([(["master", "smtpd"], 25)], ss_argv=[ss])
    assert report.ok


def test_an_expected_holder_produces_no_finding(tmp_path):
    ss = fake_tool(
        tmp_path,
        "ss",
        "echo 'LISTEN 0 100 0.0.0.0:25 0.0.0.0:* users:((\"master\",pid=1,fd=13))'",
    )
    report = preflight.check_ports([(["master", "smtpd"], 25)], ss_argv=[ss])
    assert report.ok


def test_a_squatter_is_named_in_the_finding(tmp_path):
    ss = fake_tool(
        tmp_path,
        "ss",
        "echo 'LISTEN 0 100 0.0.0.0:25 0.0.0.0:* users:((\"exim4\",pid=7,fd=3))'",
    )
    report = preflight.check_ports([(["master", "smtpd"], 25)], ss_argv=[ss])
    assert not report.ok
    finding = report.findings[0]
    assert "exim4" in finding.detail
    assert "Debian's default MTA" in finding.detail
    assert finding.remedy


def test_store_check_accepts_our_own_symlink(tmp_path):
    root = tmp_path / "nix"
    target = tmp_path / "opt/cmrelay/nix"
    target.mkdir(parents=True)
    root.symlink_to(target)
    # resolve() is what the check compares, so point it at the real location
    report = preflight.check_store(root)
    # the target is not literally /opt/cmrelay/nix here, so a finding is right
    assert not report.ok
    assert "symlink" in report.findings[0].detail


def test_store_check_refuses_an_existing_nix_directory(tmp_path):
    root = tmp_path / "nix"
    (root / "store").mkdir(parents=True)
    report = preflight.check_store(root)
    assert not report.ok
    assert "already runs Nix" in report.findings[0].detail


def test_store_check_passes_on_a_bare_host(tmp_path):
    assert preflight.check_store(tmp_path / "nowhere").ok


def test_unit_check_is_quiet_without_systemd(monkeypatch):
    monkeypatch.setattr(preflight, "systemd_available", lambda: False)
    assert preflight.check_units().ok


def test_unit_check_flags_an_enabled_distro_unit(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_tool(
        bindir,
        "systemctl",
        """
case "$1" in
  list-unit-files) echo "postfix.service enabled enabled" ;;
  is-enabled) echo enabled ;;
  is-active) echo active ;;
esac
""",
    )
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(preflight, "systemd_available", lambda: True)

    report = preflight.check_units(["postfix.service", "dovecot.service"])
    assert [f.subject for f in report.findings] == ["postfix.service"]
    assert "systemctl mask postfix.service" in report.findings[0].remedy


def test_format_report_includes_remedies():
    report = preflight.Report()
    report.add("port 25", "held by exim4", remedy="stop exim4")
    text = preflight.format_report(report)
    assert "port 25: held by exim4" in text
    assert "remedy: stop exim4" in text


def test_a_detector_that_cannot_run_is_an_error(tmp_path):
    """The worst failure mode is a clean report from a check that never ran."""
    with pytest.raises(preflight.DetectorError, match="did not run"):
        preflight.check_ports(
            [(["master"], 25)], ss_argv=[str(tmp_path / "no-such-ss")]
        )


def test_systemd_resolved_is_taken_over_rather_than_refused(tmp_path):
    """Every stock Debian and Fedora has it on port 53.

    cmdeploy stops and masks systemd-resolved as an ordinary part of every
    deploy, so refusing to install because of it would make `install` useless
    on exactly the hosts it targets.
    """
    ss = fake_tool(
        tmp_path,
        "ss",
        "echo 'LISTEN 0 4096 127.0.0.53:53 0.0.0.0:*"
        ' users:(("systemd-resolve",pid=1,fd=13))\'',
    )
    assert preflight.check_ports([(["unbound"], 53)], ss_argv=[ss]).ok


def test_another_resolver_on_53_still_refuses(tmp_path):
    ss = fake_tool(
        tmp_path,
        "ss",
        "echo 'LISTEN 0 10 0.0.0.0:53 0.0.0.0:* users:((\"named\",pid=9,fd=3))'",
    )
    report = preflight.check_ports([(["unbound"], 53)], ss_argv=[ss])
    assert not report.ok
    assert "BIND" in report.findings[0].detail
