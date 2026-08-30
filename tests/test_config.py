"""The four ini checks, and the ownership rules that turn on the config."""

import pytest

from cmrelay import activation, fsops, paths
from cmrelay import config as cfg
from cmrelay.deploy import Deployment
from cmrelay.output import Out

chatmaild = pytest.importorskip(
    "chatmaild.config", reason="chatmaild is only available in the devShell"
)


# -- reading ---------------------------------------------------------------


def test_a_missing_file_names_the_path(tmp_path):
    with pytest.raises(cfg.ConfigError, match="no configuration file"):
        cfg.read_config(tmp_path / "absent.ini")


def test_a_missing_params_section_says_what_an_ini_needs(tmp_path):
    path = tmp_path / "chatmail.ini"
    path.write_text("[other]\nx = 1\n")
    with pytest.raises(cfg.ConfigError, match="mail_domain"):
        cfg.read_config(path)


def test_a_one_path_external_cert_is_a_message_not_a_traceback(ini):
    ini.write_text(
        ini.read_text().replace("tls_cert_mode = acme", "tls_cert_mode = external")
        + "\ntls_external_cert_and_key = /only/one/path\n"
    )
    with pytest.raises(cfg.ConfigError):
        cfg.read_config(ini)


def test_an_override_must_be_key_equals_value():
    with pytest.raises(cfg.ConfigError, match="key=value"):
        cfg.parse_overrides(["nonsense"])
    assert cfg.parse_overrides(["a = 1", "b=2"]) == {"a": "1", "b": "2"}


# -- the warnings ----------------------------------------------------------


def test_a_setting_with_no_effect_is_named(ini):
    ini.write_text(ini.read_text() + "\nmax_mailbox_siz = 100\n")
    config = cfg.read_config(ini)
    assert any("max_mailbox_siz" in w for w in cfg.warnings(config, ini))


def test_privacy_contacts_naming_relays_own_operators_are_named(ini):
    config = cfg.read_config(ini)
    config.privacy_mail = "privacy@testrun.org"
    assert any("privacy_mail" in w for w in cfg.warnings(config, ini))


def test_a_testrun_domain_may_keep_them(tmp_path):
    path = tmp_path / "chatmail.ini"
    chatmaild.write_initial_config(path, "x.testrun.org", {})
    config = cfg.read_config(path)
    config.privacy_mail = "privacy@testrun.org"
    assert cfg.foreign_privacy_contacts(config) == []


def test_an_empty_acme_email_is_warned_about(ini):
    config = cfg.read_config(ini)
    config.tls_cert_mode = "acme"
    config.acme_email = ""
    assert any("acme_email" in w for w in cfg.warnings(config, ini))


def test_a_clean_ini_produces_no_warnings(ini):
    config = cfg.read_config(ini)
    config.acme_email = "postmaster@relay.example.org"
    assert cfg.warnings(config, ini) == []


# -- resolv.conf ownership -------------------------------------------------


def deployment(bundle, config, journal):
    return Deployment(bundle, config, Out(colour=False), journal)


def test_resolv_conf_is_left_alone_once_unbound_is_first(fake_root, bundle, ini):
    journal = fsops.Journal()
    paths.RESOLV_CONF.parent.mkdir(parents=True)
    paths.RESOLV_CONF.write_text("nameserver 127.0.0.1\nnameserver 10.200.200.10\n")

    deployment(bundle, cfg.read_config(ini), journal).ensure_resolver_points_here()

    assert "10.200.200.10" in paths.RESOLV_CONF.read_text()
    assert journal.changes == []


def test_resolv_conf_is_written_when_unbound_is_not_first(fake_root, bundle, ini):
    journal = fsops.Journal()
    paths.RESOLV_CONF.parent.mkdir(parents=True)
    paths.RESOLV_CONF.write_text("nameserver 8.8.8.8\n")

    deployment(bundle, cfg.read_config(ini), journal).ensure_resolver_points_here()

    assert paths.RESOLV_CONF.read_text().startswith("nameserver 127.0.0.1\n")
    assert journal.changes


def test_a_commented_first_nameserver_does_not_count(fake_root, bundle, ini):
    journal = fsops.Journal()
    paths.RESOLV_CONF.parent.mkdir(parents=True)
    paths.RESOLV_CONF.write_text("# nameserver 127.0.0.1\nnameserver 8.8.8.8\n")

    deployment(bundle, cfg.read_config(ini), journal).ensure_resolver_points_here()
    assert journal.changes


def test_a_missing_resolv_conf_is_written(fake_root, bundle, ini):
    journal = fsops.Journal()
    deployment(bundle, cfg.read_config(ini), journal).ensure_resolver_points_here()
    assert paths.RESOLV_CONF.read_text().startswith("nameserver 127.0.0.1\n")


# -- when acmetool is allowed to reach the network -------------------------


def test_a_certificate_covering_every_name_needs_no_reconcile(fake_root, bundle, ini):
    import subprocess

    config = cfg.read_config(ini)
    cert = fake_root / "cert.pem"
    key = fake_root / "key.pem"
    domains = [
        "relay.example.org",
        "mta-sts.relay.example.org",
        "www.relay.example.org",
    ]
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-noenc",
            "-days",
            "3650",
            "-subj",
            "/CN=relay.example.org",
            "-addext",
            "subjectAltName=" + ",".join(f"DNS:{d}" for d in domains),
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    config.tls_cert_path = str(cert)
    config.tls_key_path = str(key)

    unit = deployment(bundle, config, fsops.Journal())
    unit.bundle = bundle
    unit.shell = lambda *argv, **kw: subprocess.run(
        ["openssl", *[str(a) for a in argv[1:]]],
        capture_output=True,
        text=True,
        check=False,
    )
    assert activation.needs_reconcile(unit) is False


def test_a_missing_certificate_needs_a_reconcile(fake_root, bundle, ini):
    config = cfg.read_config(ini)
    config.tls_cert_path = str(fake_root / "absent.pem")
    assert (
        activation.needs_reconcile(deployment(bundle, config, fsops.Journal())) is True
    )


def test_the_reconcile_flag_forces_it(fake_root, bundle, ini):
    unit = Deployment(
        bundle, cfg.read_config(ini), Out(colour=False), fsops.Journal(), reconcile=True
    )
    assert activation.needs_reconcile(unit) is True


def test_the_deploy_plan_starts_unbound_before_opendkim(fake_root, bundle, ini):
    """opendkim reads the root anchor unbound's ExecStartPre writes."""
    plan = activation.plan(deployment(bundle, cfg.read_config(ini), fsops.Journal()))
    order = list(plan.intents)
    assert order.index("unbound.service") < order.index("opendkim.service")
    assert order.index("dovecot.service") < order.index("postfix.service")


def test_postfix_own_settings_are_carried_forward(tmp_path):
    """set-permissions appends to main.cf; rewriting it every run restarts postfix."""
    from cmrelay.daemons import STORE_MARKER, postfix_own_settings

    conf = tmp_path / "postfix"
    conf.mkdir()
    assert postfix_own_settings(conf) == ""

    (conf / "main.cf").write_text(
        "smtpd_banner = x\n"
        f"\n{STORE_MARKER}\n"
        "config_directory = /opt/cmrelay/config/postfix\n"
        "queue_directory = /var/spool/postfix\n"
        "data_directory = /var/lib/postfix/data\n"
        "command_directory = /nix/store/abc-postfix/bin\n"
        "mail_owner = postfix\n"
    )
    assert postfix_own_settings(conf) == (
        "command_directory = /nix/store/abc-postfix/bin\nmail_owner = postfix\n"
    )


def test_a_main_cf_without_the_marker_carries_nothing_forward(tmp_path):
    from cmrelay.daemons import postfix_own_settings

    conf = tmp_path / "postfix"
    conf.mkdir()
    (conf / "main.cf").write_text("smtpd_banner = x\n")
    assert postfix_own_settings(conf) == ""
