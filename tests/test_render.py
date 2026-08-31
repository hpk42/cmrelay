"""Every vendored template must render against a real relay Config.

This is the cheapest possible guard on the vendoring model. cmrelay renders
relay's templates with relay's own Config, and Jinja runs with
StrictUndefined, so a template that grows a new variable upstream fails here
rather than producing a postfix directive with an empty value on somebody's
relay.
"""

import pytest
from conftest import REPO  # noqa: I001 - added to sys.path by conftest

from cmrelay import activation, fsops, render
from cmrelay.deploy import CHATMAILD_UNITS, Deployment
from cmrelay.output import Out

chatmaild = pytest.importorskip(
    "chatmaild.config", reason="chatmaild is only available in the devShell"
)


@pytest.fixture
def config(ini):
    return chatmaild.read_config(ini)


@pytest.fixture
def deployment(bundle, config, tmp_path):
    return Deployment(bundle, config, Out(colour=False), fsops.Journal())


TEMPLATES_WITH_CONFIG = [
    "config/postfix/main.cf.j2",
    "config/postfix/master.cf.j2",
    "config/dovecot/dovecot.conf.j2",
    "config/nginx/nginx.conf.j2",
    "config/nginx/autoconfig.xml.j2",
    "config/nginx/mta-sts.txt.j2",
]


def render_config_template(deployment, config, relpath):
    return render.render_template(
        deployment.vendor(relpath),
        config=config,
        debug=False,
        disable_ipv6=config.disable_ipv6,
        **deployment.template_vars(),
    )


@pytest.mark.parametrize("relpath", TEMPLATES_WITH_CONFIG)
def test_config_templates_render(deployment, config, relpath):
    text = render_config_template(deployment, config, relpath)
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
        text = render_config_template(deployment, config, relpath)
        for needle in forbidden:
            assert needle not in text, f"{relpath} still names {needle}"


def test_postfix_config_points_at_cmrelay(deployment, config):
    text = render_config_template(deployment, config, "config/postfix/main.cf.j2")
    assert "regexp:/opt/cmrelay/config/postfix/login_map" in text
    assert "regexp:/opt/cmrelay/config/postfix/smtp_tls_policy_map" in text


def test_dovecot_quota_expire_is_a_store_path(deployment, config):
    text = render_config_template(deployment, config, "config/dovecot/dovecot.conf.j2")
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
        text = unit.read_text().format(**activation.daemon_unit_vars(deployment))
        assert "{" not in text, f"{unit.name} has an unfilled field"
        assert "[Service]" in text


def test_strict_undefined_is_an_error(tmp_path):
    from jinja2 import UndefinedError

    template = tmp_path / "t.j2"
    template.write_text("value = {{ missing }}\n")
    with pytest.raises(UndefinedError):
        render.render_template(template)


def test_the_profile_script_is_idempotent_and_valid_sh(fake_root):
    """It is sourced on every login, so it must not grow PATH each time."""
    import subprocess

    from cmrelay import installer, paths

    paths.PROFILE_D.mkdir(parents=True)
    assert installer.write_profile_script(fsops.Journal())
    script = paths.PROFILE_SCRIPT
    bin_dir = str(paths.BIN)
    # Sourcing it three times must add the directory exactly once, and it must
    # append rather than prepend so nothing here shadows a distribution tool.
    out = subprocess.run(
        ["sh", "-c", f". {script}; . {script}; . {script}; echo $PATH"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        check=True,
    ).stdout.strip()
    assert out.count(bin_dir) == 1
    assert out.endswith(f":{bin_dir}")


def test_a_distro_drop_in_anchoring_the_root_is_detected(tmp_path):
    """Two anchors for one zone is fatal to unbound, so the distro's wins."""
    drop_ins = tmp_path / "unbound.conf.d"
    drop_ins.mkdir()
    assert Deployment.anchor_already_configured(drop_ins) is False

    (drop_ins / "chatmail.conf").write_text("server:\n  cache-max-negative-ttl: 0\n")
    assert Deployment.anchor_already_configured(drop_ins) is False

    (drop_ins / "root-auto-trust-anchor-file.conf").write_text(
        'server:\n    auto-trust-anchor-file: "/var/lib/unbound/root.key"\n'
    )
    assert Deployment.anchor_already_configured(drop_ins) is True


def test_a_commented_out_anchor_does_not_count(tmp_path):
    drop_ins = tmp_path / "unbound.conf.d"
    drop_ins.mkdir()
    (drop_ins / "x.conf").write_text(
        'server:\n    # auto-trust-anchor-file: "/var/lib/unbound/root.key"\n'
    )
    assert Deployment.anchor_already_configured(drop_ins) is False


def test_a_missing_drop_in_directory_is_not_an_anchor(tmp_path):
    assert Deployment.anchor_already_configured(tmp_path / "absent") is False


def test_the_tls_policy_map_is_a_regexp_table():
    """cmrelay does not compile it, because postfix reads a regexp table as text."""
    # If this ever becomes an indexed type, postfix would look up an index
    # nobody built, and the removed postmap call would have to come back.
    template = (REPO / "vendor/config/postfix/main.cf.j2").read_text()
    line = next(
        ln for ln in template.splitlines() if ln.startswith("smtp_tls_policy_maps")
    )
    assert "regexp:" in line, line
    for indexed in ("hash:", "btree:", "lmdb:", "cdb:"):
        assert indexed not in line, line
