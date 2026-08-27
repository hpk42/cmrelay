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


def test_a_release_wheel_finds_its_payload(monkeypatch, tmp_path):
    run = tmp_path / "cmrelay.run"
    run.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setattr(payload, "BUNDLED", run)
    assert payload.bundled_payload() == run


def test_a_source_build_has_none(monkeypatch, tmp_path):
    monkeypatch.setattr(payload, "BUNDLED", tmp_path / "absent.run")
    assert payload.bundled_payload() is None


def test_without_a_payload_it_says_how_to_build_one(monkeypatch, tmp_path):
    """The failure has to name the command, not just report the absence."""
    monkeypatch.setattr(payload, "BUNDLED", tmp_path / "absent.run")
    monkeypatch.setattr(payload.sys, "platform", "linux")
    with pytest.raises(payload.PayloadError, match="nix build"):
        payload.run_from_wheel([], Recorder())


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
    """The property AlmaLinux's minimal image made visible.

    makeself's stub unpacked with the host's tar before any cmrelay code
    ran, so a host without it failed with "Extraction failed" and no way to
    say why. The header slices its own tar out of itself instead.
    """
    header = (Path(__file__).parent.parent / "stub" / "header.sh").read_text()
    assert "tail -c" in header and "head -c" in header
    for line in header.splitlines():
        bare = line.split("#", 1)[0]
        # The only tar invoked is the one sliced into $tools.
        if "tar " in bare:
            assert '"$tools/tar"' in bare or "tools/tar" in bare, line
