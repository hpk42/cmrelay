"""A failed preflight must leave the host untouched, so it is worth testing.

These exercise the parsing and the decisions, not the host: `ss` and
`systemctl` are replaced by real scripts on PATH rather than mocks, so the
code under test runs the same subprocess path it runs on a relay.
"""

import os
from types import SimpleNamespace

import pytest
from conftest import fake_tool  # noqa: I001 - added to sys.path by conftest

from cmrelay import preflight, systemd


class Config:
    """The handful of attributes relay's port table dereferences."""

    tls_cert_mode = "acme"
    filtermail_smtp_port = 10080
    filtermail_smtp_port_incoming = 10081
    filtermail_http_port_incoming = 10082
    filtermail_lmtp_port_transport = 10083
    postfix_reinject_port = 10025
    postfix_reinject_port_incoming = 10026


def test_the_table_resolves_completely():
    resolved = preflight.resolve_port_table(Config())
    ports = {port for _, port in resolved}
    assert {25, 53, 80, 143, 443, 465, 587, 993, 3340, 3903, 3904, 8443} <= ports
    assert 402 in ports  # acme mode
    assert {10080, 10081, 10082, 10083, 10025, 10026} <= ports
    assert all(isinstance(port, int) for _, port in resolved)


def test_the_acme_only_entry_drops_out_in_self_signed_mode():
    config = Config()
    config.tls_cert_mode = "self"
    ports = {port for _, port in preflight.resolve_port_table(config)}
    assert 402 not in ports


def test_a_port_setting_the_ini_lacks_is_an_error():
    class Sparse(Config):
        filtermail_smtp_port = None

    with pytest.raises(ValueError, match="filtermail_smtp_port"):
        preflight.resolve_port_table(Sparse())


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


def test_unit_check_is_quiet_without_systemd(monkeypatch):
    monkeypatch.setattr(systemd, "available", lambda: False)
    assert preflight.check_units().ok


def test_unit_check_flags_an_enabled_distro_unit(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_tool(
        bindir,
        "systemctl",
        """
cat <<'OUT'
Id=postfix.service
LoadState=loaded
ActiveState=active
UnitFileState=enabled

Id=dovecot.service
LoadState=not-found
ActiveState=inactive
UnitFileState=
OUT
""",
    )
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(systemd, "available", lambda: True)

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
    """Every stock Debian and Fedora has it on port 53; deploy takes it over."""
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


def test_the_resolver_family_covers_more_than_the_service():
    """Fedora 43 splits resolved's IPC into sockets that fail on their own."""
    assert "systemd-resolved.service" in preflight.RESOLVER_UNITS
    assert "systemd-resolved-varlink.socket" in preflight.RESOLVER_UNITS
    # These are taken over, not refused on, so they must stay out of the set
    # install refuses for -- a stock host always has resolved running.
    for name in preflight.RESOLVER_UNITS:
        assert name not in preflight.CONFLICTING_UNITS, name


# -- the external TLS and disk checks --------------------------------------


def test_a_missing_external_certificate_is_caught_before_anything_is_written(tmp_path):
    config = SimpleNamespace(
        tls_cert_mode="external",
        tls_cert_path=str(tmp_path / "cert.pem"),
        tls_key_path=str(tmp_path / "key.pem"),
    )
    report = preflight.check_external_tls(config)
    assert not report.ok
    assert len(report.findings) == 2

    (tmp_path / "cert.pem").write_text("x")
    (tmp_path / "key.pem").write_text("x")
    assert preflight.check_external_tls(config).ok


def test_other_tls_modes_are_not_checked_for_files():
    assert preflight.check_external_tls(SimpleNamespace(tls_cert_mode="acme")).ok


def test_a_full_disk_is_refused_with_a_number(tmp_path):
    report = preflight.check_free_space(2**40, tmp_path)
    assert not report.ok
    assert "MiB free" in report.findings[0].detail
    assert report.missing and not report.conflicts


def test_a_missing_mtail_says_how_to_install_it(tmp_path):
    report = preflight.check_mtail(
        "127.0.0.1", ("https://x/mtail.tgz", "abc"), tmp_path / "mtail"
    )
    assert report.missing and not report.conflicts
    assert "https://x/mtail.tgz" in report.findings[0].remedy
    assert preflight.check_mtail("", ("u", "s"), tmp_path / "mtail").ok
