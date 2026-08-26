"""The vendoring mapping has one job: never let a new relay file go unnoticed.

These tests exercise that property directly, because it is the single most
valuable thing the vendoring layer does. Everything else about vendor-sync is
a file copy.
"""

import json

import pytest
from conftest import REPO  # noqa: I001 - added to sys.path by conftest

from cmrelay.vendoring import Mapping, VendorError, plan, sha256_file, sync, verify


@pytest.fixture
def mapping():
    return Mapping.load(REPO / "vendor.toml")


def make_relay(tmp_path, files):
    for relpath, content in files.items():
        path = tmp_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def test_the_real_mapping_covers_the_real_relay(mapping, relay_root):
    """The mapping and the pinned relay commit agree, right now."""
    mapped = plan(relay_root, mapping)
    assert len(mapped) > 40
    assert "systemd/doveauth.service.f" in mapped.values()
    assert "config/postfix/main.cf.j2" in mapped.values()


def test_an_uncovered_file_is_an_error(tmp_path, mapping, relay_root):
    """A new upstream component must break the build, not vanish silently."""
    import shutil

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


def test_deployer_python_is_skipped_not_copied(mapping):
    kind, _ = mapping.classify("cmdeploy/src/cmdeploy/deployers.py")
    assert kind == "skip"
    kind, _ = mapping.classify("cmdeploy/src/cmdeploy/dovecot/deployer.py")
    assert kind == "skip"


def test_dead_mtasts_files_are_skipped(mapping):
    for name in ("mta-sts-daemon.service", "mta-sts-daemon.yml"):
        kind, _ = mapping.classify(f"cmdeploy/src/cmdeploy/postfix/{name}")
        assert kind == "skip", name


def test_units_land_in_one_flat_directory(mapping):
    for src, want in [
        (
            "cmdeploy/src/cmdeploy/service/doveauth.service.f",
            "systemd/doveauth.service.f",
        ),
        (
            "cmdeploy/src/cmdeploy/filtermail/filtermail.service.j2",
            "systemd/filtermail.service.j2",
        ),
        (
            "cmdeploy/src/cmdeploy/iroh-relay.service.j2",
            "systemd/iroh-relay.service.j2",
        ),
    ]:
        assert mapping.classify(src) == ("copy", want)


def test_colliding_destinations_are_rejected(tmp_path):
    relay = make_relay(
        tmp_path / "relay",
        {"a/one.conf": "1\n", "b/one.conf": "2\n"},
    )
    mapping = Mapping(
        relay_url="x",
        roots=["a", "b"],
        copies=[
            type(Mapping.load.__self__ or object)  # placeholder, replaced below
        ],
    )
    # built by hand rather than from TOML, to keep the test about collisions
    from cmrelay.vendoring import Rule

    mapping.copies = [Rule("a/*", "config/one.conf"), Rule("b/*", "config/one.conf")]
    with pytest.raises(VendorError, match="collide"):
        plan(relay, mapping)


def test_sync_then_verify_round_trips(tmp_path, mapping, relay_root):
    vendor = tmp_path / "vendor"
    hashes = sync(relay_root, vendor, mapping)
    assert verify(vendor, hashes) == []

    (vendor / "config/postfix/login_map").write_text("tampered\n")
    problems = verify(vendor, hashes)
    assert problems == ["modified: config/postfix/login_map"]


def test_verify_reports_extra_files(tmp_path, mapping, relay_root):
    vendor = tmp_path / "vendor"
    hashes = sync(relay_root, vendor, mapping)
    (vendor / "config" / "stray.conf").write_text("x\n")
    assert "unexpected: config/stray.conf" in verify(vendor, hashes)


def test_shipped_vendor_matches_its_lock():
    """What is committed under vendor/ is what vendor.lock says it is."""
    lock = json.loads((REPO / "vendor.lock").read_text())
    assert verify(REPO / "vendor", lock["files"]) == []


def test_lock_records_the_patch_series():
    lock = json.loads((REPO / "vendor.lock").read_text())
    series = [
        line.split("#", 1)[0].strip()
        for line in (REPO / "patches" / "series").read_text().splitlines()
    ]
    assert lock["patches"] == [s for s in series if s]


def test_patched_files_differ_from_upstream(relay_root):
    """Every patch has to actually change something, or it is dead weight."""
    lock = json.loads((REPO / "vendor.lock").read_text())
    mapping = Mapping.load(REPO / "vendor.toml")
    mapped = plan(relay_root, mapping)
    reverse = {dest: src for src, dest in mapped.items()}

    patched = [
        dest
        for dest, digest in lock["files"].items()
        if sha256_file(relay_root / reverse[dest]) != digest
    ]
    assert sorted(patched) == [
        "config/dovecot/dovecot.conf.j2",
        "config/nginx/nginx.conf.j2",
        "config/opendkim/opendkim.conf",
        "config/postfix/main.cf.j2",
        "config/postfix/master.cf.j2",
        "www/index.md",
    ]
