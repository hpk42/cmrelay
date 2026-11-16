"""Running the closure's own tools."""

import json

import pytest
from conftest import fake_tool

from cmrelay import CmrelayError
from cmrelay.release.bundle import Bundle


def test_only_the_closures_own_tools_run_and_a_failure_carries_its_output(tmp_path):
    """Never the host's: a name the manifest lacks does not fall through to PATH."""
    ok = fake_tool(tmp_path, "ok", 'echo "got $*"')
    bad = fake_tool(tmp_path, "bad", "echo broken >&2; exit 3")
    packages = {"x": {"version": "1", "ok": ok, "bad": bad, "gone": "/no/such"}}
    manifest = {"version": "1", "packages": packages}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    tools = Bundle(tmp_path)
    assert tools.run("ok", "a", 1).stdout == "got a 1\n"
    with pytest.raises(CmrelayError, match="'sh'"):
        tools.run("sh", "-c", "true")
    with pytest.raises(CmrelayError, match="(?s)failed \\(3\\).*broken"):
        tools.run("bad")
    assert tools.run("bad", check=False).returncode == 3
    with pytest.raises(CmrelayError, match="could not run /no/such"):
        tools.run("gone")
