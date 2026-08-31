"""The activate planner: intent against a snapshot, and nothing more."""

import pytest

from cmrelay import systemd


def state(enabled="enabled", active="active", load="loaded"):
    return systemd.UnitState(load, active, enabled)


ALL_GOOD = {
    "postfix.service": state(),
    "dovecot.service": state(),
    "nginx.service": state(),
}


def test_a_host_already_as_intended_issues_no_calls():
    plan = systemd.Plan()
    plan.units(["postfix", "dovecot", "nginx"])
    assert plan.calls(ALL_GOOD) == []


def test_a_stopped_unit_is_started():
    plan = systemd.Plan()
    plan.service("postfix.service")
    states = {"postfix.service": state(active="inactive")}
    assert plan.calls(states) == [["start", "postfix.service"]]


def test_a_disabled_unit_is_enabled_without_a_reload():
    plan = systemd.Plan()
    plan.service("postfix.service")
    states = {"postfix.service": state(enabled="disabled")}
    assert plan.calls(states) == [["enable", "--no-reload", "postfix.service"]]


def test_enables_batch_into_one_call():
    plan = systemd.Plan()
    plan.units(["postfix", "dovecot", "nginx"])
    states = {name: state(enabled="disabled") for name in ALL_GOOD}
    assert plan.calls(states) == [
        ["enable", "--no-reload", "dovecot.service", "nginx.service", "postfix.service"]
    ]


def test_starts_keep_the_order_they_were_declared_in():
    """opendkim will not start before unbound has written the root anchor."""
    plan = systemd.Plan()
    plan.service("unbound.service")
    plan.service("opendkim.service")
    states = {
        "unbound.service": state(active="inactive"),
        "opendkim.service": state(active="inactive"),
    }
    assert plan.calls(states) == [
        ["start", "unbound.service"],
        ["start", "opendkim.service"],
    ]


def test_starts_stay_per_unit_so_a_failure_names_one():
    plan = systemd.Plan()
    plan.units(["postfix", "dovecot"])
    states = {name: state(active="inactive") for name in ALL_GOOD}
    assert plan.calls(states) == [
        ["start", "postfix.service"],
        ["start", "dovecot.service"],
    ]


def test_a_changed_component_restarts_rather_than_starts():
    plan = systemd.Plan()
    plan.service("nginx.service", restart=True)
    assert plan.calls(ALL_GOOD) == [["restart", "nginx.service"]]


def test_a_changed_component_that_is_down_is_started_not_restarted():
    plan = systemd.Plan()
    plan.service("nginx.service", restart=True)
    assert plan.calls({"nginx.service": state(active="inactive")}) == [
        ["start", "nginx.service"]
    ]


def test_disable_mail_stops_and_disables_both():
    plan = systemd.Plan()
    plan.service("postfix.service", running=False, enabled=False)
    plan.service("dovecot.service", running=False, enabled=False)
    assert plan.calls(ALL_GOOD) == [
        ["disable", "--no-reload", "dovecot.service", "postfix.service"],
        ["stop", "postfix.service"],
        ["stop", "dovecot.service"],
    ]


def test_a_unit_this_host_does_not_have_is_left_alone():
    plan = systemd.Plan()
    plan.service("mta-sts-daemon.service", running=False, enabled=False)
    assert (
        plan.calls({"mta-sts-daemon.service": state("", "inactive", "not-found")}) == []
    )
    assert plan.calls({}) == []


def test_a_static_unit_is_not_enabled_again():
    plan = systemd.Plan()
    plan.service("fcgiwrap.service")
    assert plan.calls({"fcgiwrap.service": state(enabled="static")}) == []


def test_a_timer_driven_service_is_disabled_but_not_stopped_twice():
    plan = systemd.Plan()
    plan.service("chatmail-expire.service", running=False, enabled=False)
    states = {"chatmail-expire.service": state(enabled="disabled", active="inactive")}
    assert plan.calls(states) == []


def test_a_failed_unit_is_reset_before_anything_else():
    plan = systemd.Plan()
    plan.reset_failed.append("unbound.service")
    plan.service("unbound.service")
    states = {"unbound.service": state(active="failed")}
    assert plan.calls(states) == [
        ["reset-failed", "unbound.service"],
        ["start", "unbound.service"],
    ]


def test_journald_is_restarted_but_never_enabled():
    plan = systemd.Plan()
    plan.service("systemd-journald.service", enabled=None, restart=True)
    states = {"systemd-journald.service": state(enabled="static")}
    assert plan.calls(states) == [["restart", "systemd-journald.service"]]


# -- the state snapshot ----------------------------------------------------


def test_unit_states_parses_one_show_call(tmp_path):
    from conftest import fake_tool

    fake = fake_tool(
        tmp_path,
        "systemctl",
        """cat <<'OUT'
Id=postfix.service
LoadState=loaded
ActiveState=active
UnitFileState=enabled

Id=nosuch.service
LoadState=not-found
ActiveState=inactive
UnitFileState=
OUT""",
    )
    states = systemd.unit_states(
        ["postfix.service", "nosuch.service"], systemctl_argv=[fake]
    )
    assert states["postfix.service"].is_running
    assert states["postfix.service"].is_enabled
    assert states["nosuch.service"].exists is False


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        ("enabled", True),
        ("enabled-runtime", True),
        ("static", True),
        ("disabled", False),
    ],
)
def test_which_states_count_as_enabled(enabled, expected):
    assert state(enabled=enabled).is_enabled is expected


# -- resolver masking ------------------------------------------------------


def test_a_deploy_masks_the_whole_resolver_family(tmp_path, monkeypatch):
    """Fedora's varlink and monitor sockets fail on their own once the service goes."""
    import os

    from conftest import fake_tool

    from cmrelay import activation, preflight

    log = tmp_path / "calls"
    fake_tool(tmp_path, "systemctl", f'echo "$@" >> {log}')
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    states = {name: state() for name in preflight.RESOLVER_UNITS}
    masked = activation.mask_resolvers(states)

    assert set(masked) == set(preflight.RESOLVER_UNITS)
    assert "mask --no-reload systemd-resolved-varlink.socket" in log.read_text()


def test_the_deploy_plan_starts_unbound_before_opendkim(fake_root, bundle, ini):
    """opendkim reads the root anchor unbound's ExecStartPre writes."""
    import pytest as _p

    cfg = _p.importorskip("chatmaild.config")
    from cmrelay import activation, fsops
    from cmrelay.deploy import Deployment
    from cmrelay.output import Out

    unit = Deployment(bundle, cfg.read_config(ini), Out(colour=False), fsops.Journal())
    plan = activation.plan(unit)
    order = list(plan.intents)
    assert order.index("unbound.service") < order.index("opendkim.service")
    assert order.index("dovecot.service") < order.index("postfix.service")
