"""The hand-over from a pip-installed CLI to the payload it carries.

There is deliberately no code here that reaches the network, and no wheel
without a payload to justify any. These tests are what keeps it that way.
"""

from pathlib import Path

import pytest

from cmrelay import payload


class Recorder:
    def __init__(self):
        self.lines = []

    def section(self, text):
        self.lines.append(text)

    def print(self, text):
        self.lines.append(text)


def test_a_release_wheel_finds_its_payload(tmp_path):
    run = tmp_path / "cmrelay.run"
    run.write_bytes(b"#!/bin/sh\n")
    assert payload.bundled_payload(run) == run


def test_a_source_build_has_none(tmp_path):
    assert payload.bundled_payload(tmp_path / "absent.run") is None


def test_without_a_payload_it_says_how_to_build_one(monkeypatch, tmp_path):
    """The failure has to name the command, not just report the absence."""
    monkeypatch.setattr(payload.sys, "platform", "linux")
    with pytest.raises(payload.PayloadError, match="nix build"):
        payload.run_from_wheel([], Recorder(), bundled=tmp_path / "absent.run")


def test_install_runs_the_payload_and_nothing_else(monkeypatch, tmp_path):
    """`cmrelay install` from a wheel is the .run file, with no verb after it."""
    run = tmp_path / "cmrelay.run"
    run.write_bytes(b"#!/bin/sh\n")
    run.chmod(0o755)
    seen = {}
    monkeypatch.setattr(payload.sys, "platform", "linux")
    monkeypatch.setattr(payload.os, "execve", lambda p, a, e: seen.update(argv=a))
    payload.run_from_wheel(["install"], Recorder(), bundled=run)
    assert seen["argv"] == [str(run)]

    payload.run_from_wheel(["install", "--yes"], Recorder(), bundled=run)
    assert seen["argv"] == [str(run), "--yes"]


def test_output_flags_may_precede_the_verb(monkeypatch, tmp_path):
    """`cmrelay --verbose install` is the same request as `cmrelay install`."""
    run = tmp_path / "cmrelay.run"
    run.write_bytes(b"#!/bin/sh\n")
    run.chmod(0o755)
    seen = {}
    monkeypatch.setattr(payload.sys, "platform", "linux")
    monkeypatch.setattr(payload.os, "execve", lambda p, a, e: seen.update(argv=a))
    payload.run_from_wheel(["--verbose", "install"], Recorder(), bundled=run)
    assert seen["argv"] == [str(run), "--verbose"]


def test_every_other_verb_goes_to_the_installed_cli(monkeypatch, tmp_path):
    """The payload installs; the installed CLI is what runs a verb."""
    run = tmp_path / "cmrelay.run"
    run.write_bytes(b"#!/bin/sh\n")
    run.chmod(0o755)
    cli = tmp_path / "cmrelay"
    cli.write_bytes(b"#!/bin/sh\n")
    cli.chmod(0o755)
    seen = {}
    monkeypatch.setattr(payload.sys, "platform", "linux")
    monkeypatch.setattr(payload, "INSTALLED_CLI", cli)
    monkeypatch.setattr(payload.os, "execv", lambda p, a: seen.update(path=p, argv=a))
    payload.run_from_wheel(["deploy", "relay.example.org"], Recorder(), bundled=run)
    assert seen["path"] == str(cli)
    assert seen["argv"] == [str(cli), "deploy", "relay.example.org"]


def test_a_verb_before_installing_says_to_install(monkeypatch, tmp_path):
    """Nothing installs behind the operator's back, so it names the command."""
    run = tmp_path / "cmrelay.run"
    run.write_bytes(b"#!/bin/sh\n")
    run.chmod(0o755)
    monkeypatch.setattr(payload.sys, "platform", "linux")
    monkeypatch.setattr(payload, "INSTALLED_CLI", tmp_path / "absent")
    with pytest.raises(payload.PayloadError, match="cmrelay install"):
        payload.run_from_wheel(["deploy", "relay.example.org"], Recorder(), bundled=run)


def test_the_wheel_and_the_closure_agree_on_where_the_cli_lands(monkeypatch):
    from cmrelay import paths

    assert payload.INSTALLED_CLI == paths.LOCAL_BIN / "cmrelay"


def test_a_non_executable_payload_is_made_executable(monkeypatch, tmp_path):
    """pip records mode 755, but not every way of unpacking a wheel keeps it."""
    run = tmp_path / "cmrelay.run"
    run.write_bytes(b"#!/bin/sh\n")
    run.chmod(0o644)
    seen = {}
    monkeypatch.setattr(
        payload.os, "execve", lambda p, a, e: seen.update(path=p, env=e)
    )
    payload.exec_payload(run, ["install"])
    assert run.stat().st_mode & 0o111
    assert seen["path"] == str(run)


def test_tmpdir_defaults_off_the_tmpfs(monkeypatch, tmp_path):
    """The closure is ~250 MiB open and /tmp is a tmpfs on several targets."""
    run = tmp_path / "cmrelay.run"
    run.write_bytes(b"#!/bin/sh\n")
    run.chmod(0o755)
    seen = {}
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.setattr(payload.os, "execve", lambda p, a, e: seen.update(env=e))
    payload.exec_payload(run, [])
    assert seen["env"]["TMPDIR"] == "/var/tmp"


def test_nothing_in_the_module_can_reach_the_network():
    """The property the design rests on, asserted rather than assumed."""
    source = Path(payload.__file__).read_text()
    for banned in ("urllib", "http", "socket", "requests", "releases/download"):
        assert banned not in source, banned


def test_the_header_needs_no_host_tar():
    """The header slices its own tar out of itself; a host may ship none."""
    header = (Path(__file__).parent.parent / "stub" / "header.sh").read_text()
    assert "tail -c" in header and "head -c" in header
    for line in header.splitlines():
        bare = line.split("#", 1)[0]
        # The only tar invoked is the one sliced into $tools.
        if "tar " in bare:
            assert '"$tools/tar"' in bare or "tools/tar" in bare, line


def test_the_header_refuses_arguments_before_it_unpacks():
    """Otherwise a mistyped domain costs a 50 MiB unpack to be told so."""
    import subprocess

    header = Path(__file__).parent.parent / "stub" / "header.sh"
    refused = subprocess.run(
        ["sh", str(header), "relay.example.org"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode == 1
    assert "takes no arguments" in refused.stderr
    assert "cmrelay deploy relay.example.org" in refused.stderr

    # -y, --stage-only and -- are its own, so they get past the check and on
    # to the root test, which is what stops a suite that is not root.
    allowed = subprocess.run(
        ["sh", str(header), "-y", "--stage-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "takes no arguments" not in allowed.stderr
    assert "must run as root" in allowed.stderr

    # A verb is quoted as itself rather than handed to deploy.
    verb = subprocess.run(
        ["sh", str(header), "status"], capture_output=True, text=True, check=False
    )
    assert "cmrelay status" in verb.stderr


def test_the_header_and_the_cli_name_the_same_verbs():
    """The stub's hint tells a verb from a domain, so it lists them too."""
    from cmrelay.installer import VERBS

    header = (Path(__file__).parent.parent / "stub" / "header.sh").read_text()
    listed = [line for line in header.splitlines() if "| upgrade |" in line]
    assert listed, "the stub no longer lists the verbs its hint switches on"
    for name in VERBS:
        assert name in listed[0], name


def test_the_header_uses_cmrelays_names():
    """Eight literals the stub shares with bundle.py, paths.py and closure.nix."""
    from cmrelay import paths
    from cmrelay.bundle import (
        BOOTSTRAP,
        ENV_CLOSURE,
        ENV_INSTALLER,
        ENV_STAGE,
        READY_MARKER,
    )

    repo = Path(__file__).parent.parent
    header = (repo / "stub/header.sh").read_text()
    closure_nix = (repo / "nix/closure.nix").read_text()

    assert f"ready={READY_MARKER}" in header
    assert BOOTSTRAP in header
    assert f"export {ENV_STAGE} {ENV_CLOSURE} {ENV_INSTALLER}" in header
    assert f"shared={paths.STAGING}" in header
    assert str(paths.STORE) in header
    for key in ("CMRELAY_LD", "CMRELAY_PYTHON", "CMRELAY_SITE"):
        assert key in header, key
        assert key in closure_nix, key
