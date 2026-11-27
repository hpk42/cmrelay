"""Which units a deploy enables, starts and restarts."""

import pytest
from conftest import fake_tool

from cmrelay import CmrelayError, paths
from cmrelay.cli.deploy import activate, retire
from cmrelay.stack.deployment import Deployment
from cmrelay.stack.opendkim import opendkim
from cmrelay.system import fsops, systemd


def state(enabled="enabled", active="active", load="loaded"):
    return systemd.UnitState(load, active, enabled)


def test_only_what_this_run_changed_restarts(systemctl):
    rows = {
        "nginx.service": (True, True, state()),
        "postfix.service": (True, False, state(active="inactive")),
        "dovecot.service": (False, True, state()),
        "systemd-journald.service": (None, True, state(enabled="static")),
        "mta-sts-daemon.service": (False, False, state("", "inactive", "not-found")),
    }
    units = {name: row[:2] for name, row in rows.items()}
    states = {name: row[2] for name, row in rows.items()}
    log = systemctl()
    assert sorted(systemd.apply(units, states)) == [
        ("dovecot.service", "disable"),
        ("dovecot.service", "stop"),
        ("nginx.service", "restart"),
        ("postfix.service", "start"),
        ("systemd-journald.service", "restart"),
    ]
    calls = log.read_text().splitlines()
    assert "restart nginx.service systemd-journald.service" in calls
    assert "mta-sts" not in log.read_text()


def test_what_the_configuration_does_not_ask_for_is_off(fake_root, deployment):
    # an edited ini restarts the units that read it
    deployment.changes.write("chatmail.ini", fake_root / "digest", "changed\n")
    units = deployment.units()
    assert units["doveauth.service"][1] and units["filtermail.service"][1]
    assert not units["nginx.service"][1]

    deployment.config.ipv4_relay = "203.0.113.10"
    deployment.config.tls_cert_mode = "self"
    deployment.config.mtail_address = None
    opendkim.configure(deployment)

    assert "opendkim.service" not in paths.own_units()
    wanted = deployment.units()
    off = ["opendkim.service", "mtail.service", "acmetool-redirector.service"]
    off += ["acmetool-reconcile.timer", "tls-cert-reload.path"]
    assert [wanted[name][0] for name in off] == [False] * len(off)
    # a stop would cut short what its timer started
    assert wanted["chatmail-expire.service"] == (None, False)


def test_without_a_certificate_everything_but_its_users_starts(
    fake_root, capsys, deployment, systemctl, tmp_path
):
    """unbound above all: resolv.conf already names it."""
    acmetool = fake_tool(tmp_path, "acmetool", "exit 1")
    deployment.bundle.packages["acmetool"]["acmetool"] = acmetool
    deployment.config.tls_cert_path = str(tmp_path / "no-such-cert")
    log = systemctl("loaded inactive disabled")

    activate(deployment, deployment.changes, deployment.out)

    [started] = [ln for ln in log.read_text().splitlines() if ln.startswith("start ")]
    assert "unbound.service" in started and "filtermail.service" in started
    for name in Deployment.TLS_UNITS:
        assert name not in started
    assert "all as intended" not in capsys.readouterr().out


def test_a_unit_an_old_release_wrote_goes(fake_root, deployment, tmp_path):
    acmetool = fake_tool(tmp_path, "acmetool", "exit 1")
    deployment.bundle.packages["acmetool"]["acmetool"] = acmetool
    deployment.config.tls_cert_path = str(tmp_path / "no-such-cert")
    paths.SYSTEMD_SYSTEM.mkdir(parents=True)
    names = ("chatmail-fsreport.timer", "nginx.service", "backup.timer")
    stale, kept, operators = (paths.SYSTEMD_SYSTEM / n for n in names)
    stale.write_text(f"{paths.MARKER} 0.6.0\n[Timer]\n")
    kept.write_text(f"{paths.MARKER} 0.6.0\n[Service]\n")
    operators.write_text("[Timer]\n")

    activate(deployment, deployment.changes, deployment.out)

    assert not stale.exists() and kept.exists() and operators.exists()


def test_retiring_masks_what_it_can_and_refuses_a_failure(fake_root, out, systemctl):
    paths.SYSTEMD_SYSTEM.mkdir(parents=True, exist_ok=True)
    (paths.SYSTEMD_SYSTEM / "mta-sts-daemon.service").write_text("[Unit]\n")
    log = systemctl()
    names = ["exim4.service", "mta-sts-daemon.service"]

    assert retire(names, fsops.Changes(), out, mask=True) == names
    assert "mask --no-reload mta-sts" not in log.read_text()
    undo = ["enable exim4.service", "enable mta-sts-daemon.service"]
    assert fsops.read_lines(paths.UNDO) == [*undo, "unmask exim4.service"]

    fake = log.parent / "systemctl"
    fake_tool(
        log.parent, "systemctl", '[ "$1" != mask ] || exit 1\n' + fake.read_text()
    )
    with pytest.raises(CmrelayError, match="systemctl mask failed"):
        retire(["postfix@-.service"], fsops.Changes(), out, mask=True)


def test_verify_names_a_wanted_unit_not_running(fake_root, deployment, systemctl):
    systemctl("loaded activating enabled")
    problems = deployment.verify()
    assert "filtermail.service is not running (activating)" in problems
    assert not any("mtail" in p for p in problems)
