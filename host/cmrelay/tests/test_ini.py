"""The ini checks, and when acmetool reaches the network."""

import shutil
import subprocess

import pytest

from cmrelay import CmrelayError
from cmrelay.stack import config as cfg
from cmrelay.stack.tls import tls


def test_a_missing_or_malformed_ini_is_a_message_not_a_traceback(tmp_path, ini):
    with pytest.raises(CmrelayError, match="cmrelay init"):
        cfg.read_config(tmp_path / "absent.ini")
    (tmp_path / "other.ini").write_text("[other]\nx = 1\n")
    with pytest.raises(CmrelayError, match="mail_domain"):
        cfg.read_config(tmp_path / "other.ini")
    ini.write_text(ini.read_text() + "\ntls_external_cert_and_key = /only/one/path\n")
    with pytest.raises(CmrelayError):
        cfg.read_config(ini)


def test_the_warnings_name_what_is_wrong(ini):
    config = cfg.read_config(ini)
    config.acme_email = "postmaster@relay.example.org"
    assert cfg.warnings(config, ini) == []

    config.acme_email = ""
    assert any("acme_email" in w for w in cfg.warnings(config, ini))
    ini.write_text(ini.read_text() + "\nmax_mailbox_siz = 100\nssh_host = relay\n")
    [typo, _] = cfg.warnings(cfg.read_config(ini), ini)
    assert "max_mailbox_siz" in typo
    # an adopted ini names a path on a build machine: ignored, and said so
    config.www_folder = "/home/me/site"
    assert any("www_folder = /home/me/site" in w for w in cfg.warnings(config, ini))


def test_only_a_certificate_valid_for_a_week_needs_no_reconcile(fake_root, deployment):
    config, cert = deployment.config, fake_root / "cert.pem"
    config.tls_cert_path = str(cert)
    assert tls.needs_reconcile(deployment) is True

    def issue(days):
        argv = f"openssl req -x509 -newkey rsa:2048 -noenc -days {days}".split()
        argv += ["-subj", "/CN=relay.example.org", "-keyout", fake_root / "key.pem"]
        subprocess.run([*argv, "-out", cert], check=True, capture_output=True)

    deployment.bundle.packages["openssl"]["openssl"] = shutil.which("openssl")
    issue(3)
    assert tls.needs_reconcile(deployment) is True
    issue(30)
    assert tls.needs_reconcile(deployment) is False
