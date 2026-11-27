"""The command surface."""

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

from conftest import fake_tool

from cmrelay import CmrelayError, paths
from cmrelay.cli import main as cli
from cmrelay.cli import status, uninstall
from cmrelay.stack.unbound import unbound
from cmrelay.system import prechecks, systemd


def test_the_manual_shows_what_the_code_does():
    """What doc/regen.py --update would change is printed as a diff."""
    path = Path(__file__).parents[3] / "doc/regen.py"
    spec = importlib.util.spec_from_file_location("regen", path)
    regen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(regen)
    assert regen.main([]) == 0


def test_a_refusal_is_one_message_and_exit_one(monkeypatch, capsys, tmp_path):
    def refuse(args, bundle, out):
        raise CmrelayError("nothing doing")

    monkeypatch.setenv("CMRELAY_CLOSURE", str(tmp_path))
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0)
    monkeypatch.setitem(cli.VERBS, "status", refuse)
    assert cli.main(["status"]) == 1
    assert "error: nothing doing" in capsys.readouterr().out


def test_uninstall_removes_only_its_own(fake_root, bundle, out, monkeypatch, systemctl):
    monkeypatch.setattr(systemd, "tmpfiles_create", lambda: None)
    link, theirs, script = (paths.LOCAL_BIN / n for n in ("cmrelay", "x", "y"))
    link.parent.mkdir(parents=True)
    link.symlink_to(paths.CLI)
    theirs.symlink_to("/usr/bin/x")
    script.write_text("#!/bin/sh\n")
    paths.UNDO.parent.mkdir(parents=True)
    paths.UNDO.write_text("unmask exim4.service\nunmask systemd-resolved.service\n")
    # the deploy replaced this stub link, uninstall restores it
    stub = "../run/systemd/resolve/stub-resolv.conf"
    unbound.RESOLV_CONF_SAVED.symlink_to(stub)
    unbound.RESOLV_CONF.parent.mkdir(parents=True, exist_ok=True)
    unbound.RESOLV_CONF.write_text("nameserver 127.0.0.1\n")
    paths.VERSION_MARKER.parent.mkdir(parents=True, exist_ok=True)
    paths.VERSION_MARKER.write_text("cae03e2714\n")
    # a deploy's drop-in goes, the operator's beside it stays
    ours, operators = (paths.JOURNALD_DROPINS / n for n in ("90-x.conf", "50-y.conf"))
    ours.parent.mkdir(parents=True)
    ours.write_text(f"{paths.MARKER} 1.0\n[Journal]\n")
    operators.write_text("[Journal]\n")

    # a prompt nobody can answer is a no
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert uninstall.uninstall_verb(SimpleNamespace(yes=False), bundle, out) == 1
    assert link.is_symlink() and paths.ROOT.is_dir()
    assert uninstall.uninstall_verb(SimpleNamespace(yes=True), bundle, out) == 0
    assert not link.is_symlink() and theirs.is_symlink() and script.is_file()
    assert not paths.ROOT.exists() and not paths.VERSION_MARKER.exists()
    assert not ours.exists() and operators.exists()
    calls = systemctl().read_text()
    assert "unmask --no-reload exim4.service" in calls
    assert str(unbound.RESOLV_CONF.readlink()) == stub
    assert "start systemd-resolved.service" in calls


def test_status_counts_units_not_running_and_conflicts(
    capsys, fake_root, bundle, ini, systemctl, out, monkeypatch, tmp_path
):
    monkeypatch.setattr(prechecks, "run_prechecks", lambda *args: [])
    paths.INI.parent.mkdir(parents=True)
    paths.INI.write_text(ini.read_text())
    paths.SYSTEMD_SYSTEM.mkdir(parents=True)
    for name in ("nginx.service", "postfix.service"):
        (paths.SYSTEMD_SYSTEM / name).write_text(f"{paths.MARKER}\n")

    assert status.status_verb(None, bundle, out) == 0
    assert "units            2, all good\n" in capsys.readouterr().out

    # a unit restarting in a loop is "activating", which is not running either
    systemctl("loaded activating enabled")
    assert status.status_verb(None, bundle, out) == 1
    assert "units            2, 2 not as intended\n" in capsys.readouterr().out

    systemctl("loaded failed enabled")
    conflict = (prechecks.CONFLICT, "exim4.service", "enabled/active", "mask it")
    monkeypatch.setattr(prechecks, "run_prechecks", lambda *args: [conflict])
    assert status.status_verb(None, bundle, out) == 1
    printed = capsys.readouterr().out
    assert "units            2, 2 not as intended\n" in printed
    assert "exim4.service" in printed

    # a failed unit of cmrelay's own counts once, as a unit not as intended
    systemctl(failed=["postfix.service", "systemd-networkd-wait-online.service"])
    monkeypatch.setattr(prechecks, "run_prechecks", lambda *args: [])
    assert status.status_verb(None, bundle, out) == 1
    printed = capsys.readouterr().out
    assert "not green: 1 failed host unit; see below" in printed
    assert "systemctl status systemd-networkd-wait-online.service" in printed

    # a local resolver that does not answer, while resolv.conf names it first
    systemctl()
    unbound.RESOLV_CONF.parent.mkdir(parents=True, exist_ok=True)
    unbound.RESOLV_CONF.write_text("nameserver 127.0.0.1\n")
    bundle.packages["ldns"]["drill"] = fake_tool(tmp_path, "drill", "exit 1")
    assert status.status_verb(None, bundle, out) == 1
    assert "not green: 1 silent resolver; see below" in capsys.readouterr().out
    fake_tool(tmp_path, "drill", "exit 0")
    assert status.status_verb(None, bundle, out) == 0

    (paths.SYSTEMD_SYSTEM / "chatmail-fsreport.timer").write_text(f"{paths.MARKER}\n")
    assert status.status_verb(None, bundle, out) == 1
    printed = capsys.readouterr().out
    assert "units            3, 1 not as intended\n" in printed
    assert "chatmail-fsreport.timer" in printed
