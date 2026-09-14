"""The installer stub and what it hands over to."""

import subprocess
from pathlib import Path

from conftest import REPO  # noqa: I001 - added to sys.path by conftest

from cmrelay import fsops, installer, paths
from cmrelay.bundle import ENV_CLOSURE, ENV_STAGE, READY_MARKER

HEADER = REPO / "stub" / "header.sh"


def test_the_header_refuses_arguments_before_it_unpacks():
    """Otherwise a mistyped domain costs a full unpack to be told so."""
    refused = subprocess.run(
        ["sh", str(HEADER), "relay.example.org"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode == 1
    assert "takes no arguments" in refused.stderr
    assert "cmrelay deploy DOMAIN" in refused.stderr

    # -y and --stage-only are its own, so they get past the check and on to
    # the root test, which is what stops a suite that is not root.
    allowed = subprocess.run(
        ["sh", str(HEADER), "-y", "--stage-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "takes no arguments" not in allowed.stderr
    assert "must run as root" in allowed.stderr


def test_the_header_needs_no_host_tar():
    """It slices its own tar out of itself; a host may ship none."""
    header = HEADER.read_text()
    assert "tail -c" in header and "head -c" in header
    for line in header.splitlines():
        bare = line.split("#", 1)[0]
        if "tar " in bare:
            assert "tools/tar" in bare, line


def test_the_header_uses_cmrelays_names():
    """The literals the stub shares with bundle.py, paths.py and closure.nix."""
    header = HEADER.read_text()
    closure_nix = (REPO / "nix/closure.nix").read_text()
    assert f"ready={READY_MARKER}" in header
    assert f"export {ENV_STAGE} {ENV_CLOSURE}" in header
    assert f"shared={paths.STAGING}" in header
    assert str(paths.STORE) in header
    for key in ("CMRELAY_LD", "CMRELAY_PYTHON", "CMRELAY_SITE"):
        assert key in header and key in closure_nix, key


def test_the_profile_script_is_idempotent_and_valid_sh(fake_root):
    """It is sourced on every login, so it must not grow PATH each time."""
    paths.PROFILE_D.mkdir(parents=True)
    assert installer.write_profile_script(fsops.Journal())
    script = paths.PROFILE_SCRIPT
    bin_dir = str(paths.BIN)
    out = subprocess.run(
        ["sh", "-c", f". {script}; . {script}; . {script}; echo $PATH"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        check=True,
    ).stdout.strip()
    assert out.count(bin_dir) == 1
    assert out.endswith(f":{bin_dir}")
    assert Path(script).read_text().startswith("# written by cmrelay")
