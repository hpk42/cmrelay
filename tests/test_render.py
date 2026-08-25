"""Every vendored template must render against a real relay Config.

This is the cheapest possible guard on the vendoring model. cmrelay renders
relay's templates with relay's own Config, and Jinja runs with
StrictUndefined, so a template that grows a new variable upstream fails here
rather than producing a postfix directive with an empty value on somebody's
relay.
"""

import pytest
from conftest import REPO  # noqa: I001 - added to sys.path by conftest

from cmrelay import render
from cmrelay.deploy import CHATMAILD_UNITS, Deployment
from cmrelay.output import Out

chatmaild = pytest.importorskip(
    "chatmaild.config", reason="chatmaild is only available in the devShell"
)


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "chatmail.ini"
    chatmaild.write_initial_config(path, "relay.example.org", {})
    return chatmaild.read_config(path)


@pytest.fixture
def deployment(bundle, config, tmp_path):
    return Deployment(bundle, config, Out(colour=False))


TEMPLATES_WITH_CONFIG = [
    "config/postfix/main.cf.j2",
    "config/postfix/master.cf.j2",
    "config/dovecot/dovecot.conf.j2",
    "config/nginx/nginx.conf.j2",
    "config/nginx/autoconfig.xml.j2",
    "config/nginx/mta-sts.txt.j2",
]


@pytest.mark.parametrize("relpath", TEMPLATES_WITH_CONFIG)
def test_config_templates_render(deployment, config, relpath):
    text = render.render_template(
        deployment.vendor(relpath),
        config=config,
        debug=False,
        disable_ipv6=config.disable_ipv6,
        **deployment.template_vars(),
    )
    assert text
    assert "{{" not in text


def test_no_debian_paths_survive_rendering(deployment, config):
    """The patch series exists to remove exactly these."""
    forbidden = [
        "/usr/local/lib/chatmaild",
        "/usr/lib/cgi-bin",
        "/etc/nginx/",
        "/etc/dovecot/",
        "/usr/share/dovecot/",
        "load_module",
    ]
    for relpath in TEMPLATES_WITH_CONFIG:
        text = render.render_template(
            deployment.vendor(relpath),
            config=config,
            debug=False,
            disable_ipv6=config.disable_ipv6,
            **deployment.template_vars(),
        )
        for needle in forbidden:
            assert needle not in text, f"{relpath} still names {needle}"


def test_postfix_config_points_at_cmrelay(deployment, config):
    text = render.render_template(
        deployment.vendor("config/postfix/main.cf.j2"),
        config=config,
        disable_ipv6=config.disable_ipv6,
        **deployment.template_vars(),
    )
    assert "regexp:/opt/cmrelay/config/postfix/login_map" in text
    assert "regexp:/opt/cmrelay/config/postfix/smtp_tls_policy_map" in text


def test_dovecot_quota_expire_is_a_store_path(deployment, config):
    text = render.render_template(
        deployment.vendor("config/dovecot/dovecot.conf.j2"),
        config=config,
        debug=False,
        disable_ipv6=config.disable_ipv6,
        **deployment.template_vars(),
    )
    assert "executable = script /nix/store/" in text
    assert "chatmail-quota-expire" in text


def test_chatmaild_units_render_with_store_paths(deployment):
    for unit in CHATMAILD_UNITS:
        basename = unit if "." in unit else f"{unit}.service"
        script = unit.split(".")[0]
        text = render.render_format(
            deployment.vendor(f"systemd/{basename}.f"),
            execpath=deployment.bin("python", script),
            config_path="/opt/cmrelay/chatmail.ini",
            remote_venv_dir=deployment.bin("python", "path"),
            mail_domain="relay.example.org",
        )
        assert "[Unit]" in text
        assert "/usr/local/lib/chatmaild" not in text


def test_filtermail_units_render(deployment):
    for name in ("filtermail", "filtermail-incoming", "filtermail-transport"):
        text = render.render_template(
            deployment.vendor(f"systemd/{name}.service.j2"),
            bin_path=deployment.bin("filtermail"),
            config_path="/opt/cmrelay/chatmail.ini",
        )
        assert "ExecStart=/nix/store/" in text


def test_opendkim_conf_renders_with_a_trust_anchor(deployment):
    text = render.render_template(
        deployment.vendor("config/opendkim/opendkim.conf"),
        config={"domain_name": "relay.example.org", "opendkim_selector": "opendkim"},
        trust_anchor_file="/var/lib/unbound/root.key",
    )
    assert "TrustAnchorFile\t\t/var/lib/unbound/root.key" in text
    assert "/usr/share/dns/root.key" not in text


def test_authored_units_have_every_field_supplied(deployment):
    """The five daemon units cmrelay writes itself."""
    for unit in sorted((REPO / "units").glob("*.service")):
        text = unit.read_text().format(**deployment.daemon_unit_vars())
        assert "{" not in text, f"{unit.name} has an unfilled field"
        assert "[Service]" in text


def test_write_reports_change_only_when_content_differs(tmp_path):
    target = tmp_path / "a.conf"
    assert render.write(target, "one\n") is True
    assert render.write(target, "one\n") is False
    assert render.write(target, "two\n") is True
    assert target.read_text() == "two\n"


def test_strict_undefined_is_an_error(tmp_path):
    from jinja2 import UndefinedError

    template = tmp_path / "t.j2"
    template.write_text("value = {{ missing }}\n")
    with pytest.raises(UndefinedError):
        render.render_template(template)
