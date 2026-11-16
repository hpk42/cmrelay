"""`cmrelay init`."""

from types import SimpleNamespace

import pytest

from cmrelay import CmrelayError, paths
from cmrelay.cli.init import init_domain, init_verb


def test_a_cmdeploy_relay_keeps_its_own_domain(fake_root):
    paths.CMDEPLOY_INI.parent.mkdir(parents=True)
    paths.CMDEPLOY_INI.write_text("[params]\nmail_domain = old.example.org\n")
    assert init_domain(None) == "old.example.org"
    assert init_domain("old.example.org") == "old.example.org"
    with pytest.raises(CmrelayError, match="not 'new.example.org'"):
        init_domain("new.example.org")


def test_init_writes_the_ini_once(fake_root, out):
    args = SimpleNamespace(domain="relay.example.org")
    with pytest.raises(CmrelayError, match="needs a domain"):
        init_verb(SimpleNamespace(domain=None), None, out)

    assert init_verb(args, None, out) == 0
    assert "mail_domain = relay.example.org" in paths.INI.read_text()
    with pytest.raises(CmrelayError, match="not modifying"):
        init_verb(args, None, out)
