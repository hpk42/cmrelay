"""The vendoring mapping has one job: never let a new relay file go unnoticed."""

import json
import shutil
import textwrap

import pytest
from conftest import REPO  # noqa: I001 - added to sys.path by conftest

from cmrelay.vendoring import (
    Mapping,
    Rule,
    VendorError,
    apply_patches,
    plan,
    read_pins,
    refresh,
)


@pytest.fixture
def mapping():
    return Mapping.load(REPO / "vendor.toml")


def test_the_real_mapping_covers_the_real_relay(mapping, relay_root):
    mapped = plan(relay_root, mapping)
    assert len(mapped) > 40
    assert "systemd/doveauth.service.f" in mapped.values()
    assert "config/dovecot/auth.lua.j2" in mapped.values()


def test_an_uncovered_file_is_an_error(tmp_path, mapping, relay_root):
    """A new upstream component must break the build, not vanish silently."""
    fake = tmp_path / "relay"
    shutil.copytree(
        relay_root / "cmdeploy",
        fake / "cmdeploy",
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(relay_root / "www/src", fake / "www/src", symlinks=True)
    (fake / "cmdeploy/src/cmdeploy/newthing").mkdir(parents=True)
    (fake / "cmdeploy/src/cmdeploy/newthing/newthing.conf.j2").write_text("x\n")

    with pytest.raises(VendorError) as excinfo:
        plan(fake, mapping)
    assert "newthing.conf.j2" in str(excinfo.value)
    assert "vendor.toml" in str(excinfo.value)


def test_deployer_python_is_skipped_and_units_land_flat(mapping):
    assert mapping.classify("cmdeploy/src/cmdeploy/deployers.py")[0] == "skip"
    assert mapping.classify("cmdeploy/src/cmdeploy/dovecot/deployer.py")[0] == "skip"
    assert mapping.classify("cmdeploy/src/cmdeploy/postfix/mta-sts-daemon.yml")[0] == (
        "skip"
    )
    assert mapping.classify("cmdeploy/src/cmdeploy/service/doveauth.service.f") == (
        "copy",
        "systemd/doveauth.service.f",
    )
    assert mapping.classify("cmdeploy/src/cmdeploy/iroh-relay.service.j2") == (
        "copy",
        "systemd/iroh-relay.service.j2",
    )


def test_colliding_destinations_are_rejected(tmp_path):
    for relpath in ("a/one.conf", "b/one.conf"):
        (tmp_path / relpath).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relpath).write_text("x\n")
    mapping = Mapping(
        roots=["a", "b"],
        copies=[Rule("a/*", "config/one.conf"), Rule("b/*", "config/one.conf")],
    )
    with pytest.raises(VendorError, match="collide"):
        plan(tmp_path, mapping)


def test_the_shipped_tree_is_what_a_fresh_refresh_produces(relay_root, tmp_path):
    """What is committed is what scripts/vendor-check verifies in CI."""
    refresh(REPO, relay_root, tmp_path)
    fresh = {
        p.relative_to(tmp_path / "vendor"): p.read_bytes()
        for p in (tmp_path / "vendor").rglob("*")
        if p.is_file()
    }
    shipped = {
        p.relative_to(REPO / "vendor"): p.read_bytes()
        for p in (REPO / "vendor").rglob("*")
        if p.is_file()
    }
    assert fresh == shipped
    for name in ("vendor.lock", "nix/generated/relay-pins.json"):
        assert (tmp_path / name).read_bytes() == (REPO / name).read_bytes()


def test_every_patch_changes_something(relay_root, mapping):
    """A patch that no longer applies to anything is dead weight."""
    lock = json.loads((REPO / "vendor.lock").read_text())
    series = [
        line.split("#", 1)[0].strip()
        for line in (REPO / "patches" / "series").read_text().splitlines()
    ]
    assert lock["patches"] == [s for s in series if s]

    reverse = {dest: src for src, dest in plan(relay_root, mapping).items()}
    patched = [
        dest
        for dest, src in reverse.items()
        if (REPO / "vendor" / dest).read_bytes() != (relay_root / src).read_bytes()
    ]
    assert sorted(patched) == [
        "pycode/dns.py",
        "pycode/rdns.py",
        "www/index.md",
    ]


def test_a_patch_applied_at_an_offset_leaves_no_backup(tmp_path):
    (tmp_path / "patches").mkdir()
    (tmp_path / "patches/series").write_text("one.patch\n")
    (tmp_path / "patches/one.patch").write_text(
        "--- a/one.conf\n+++ b/one.conf\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n"
    )
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "one.conf").write_text("new\na\nb\n")
    apply_patches(tmp_path, vendor)
    assert [p.name for p in vendor.iterdir()] == ["one.conf"]
    assert (vendor / "one.conf").read_text() == "new\na\nc\n"


# -- relay's pins ----------------------------------------------------------


def write(tmp_path, source):
    path = tmp_path / "pins.py"
    path.write_text(textwrap.dedent(source))
    return path


def test_the_real_pins_name_both_architectures(relay_root):
    pins = read_pins(relay_root / "cmdeploy/src/cmdeploy/pins.py")
    for table in (
        "FILTERMAIL_ARTIFACTS",
        "MTAIL_ARTIFACTS",
        "TURN_ARTIFACTS",
        "IROH_ARTIFACTS",
    ):
        assert {"x86_64", "aarch64"} <= set(pins[table])
        for url, digest in pins[table].values():
            assert url.startswith("https://")
            assert len(digest) == 64


def test_a_pins_module_that_imports_is_refused(tmp_path):
    """Running upstream code is only acceptable while it pulls in nothing."""
    path = write(
        tmp_path,
        """
        import os

        FILTERMAIL_ARTIFACTS = {}
        MTAIL_ARTIFACTS = {}
        TURN_ARTIFACTS = {}
        IROH_ARTIFACTS = {}
    """,
    )
    with pytest.raises(VendorError, match="imports something"):
        read_pins(path)


def test_a_missing_artifact_table_is_an_error(tmp_path):
    path = write(tmp_path, "FILTERMAIL_ARTIFACTS = {}\nMTAIL_ARTIFACTS = {}\n")
    with pytest.raises(VendorError, match="TURN_ARTIFACTS, IROH_ARTIFACTS"):
        read_pins(path)


def test_values_json_cannot_express_are_dropped(tmp_path):
    """relay keys DOVECOT_SHA256 by tuples; cmrelay builds dovecot itself."""
    path = write(
        tmp_path,
        """
        FILTERMAIL_ARTIFACTS = {"x86_64": ("https://example.org/x", "a")}
        MTAIL_ARTIFACTS = {}
        TURN_ARTIFACTS = {}
        IROH_ARTIFACTS = {}
        DOVECOT_SHA256 = {("core", "amd64"): "deadbeef"}
    """,
    )
    pins = read_pins(path)
    assert "DOVECOT_SHA256" not in pins
    assert pins["FILTERMAIL_ARTIFACTS"]["x86_64"] == ("https://example.org/x", "a")
