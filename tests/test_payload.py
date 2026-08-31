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
