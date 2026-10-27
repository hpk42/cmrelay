"""The staging prefix, which is bundle.py's whole reason to exist.

Two path questions have opposite answers and it is easy to confuse them.
Where a file *is right now* has the staging root in front of it; what a unit
file or a rendered config should *say* never does, because those name what
the binary will be once the store is installed. Getting that backwards bakes
a temporary directory into a relay's configuration.
"""

from pathlib import Path

import pytest

from cmrelay.bundle import Bundle, BundleError


def test_bin_never_carries_the_staging_prefix(bundle):
    """What lands in a unit file has to be the installed path."""
    assert bundle.prefix is not None
    assert bundle.bin("filtermail").startswith("/nix/store/")
    assert str(bundle.prefix) not in bundle.bin("filtermail")


def test_resolve_finds_the_file_where_it_actually_is(bundle):
    resolved = bundle.resolve(bundle.bin("filtermail"))
    assert str(resolved).startswith(str(bundle.prefix))
    # staging stands in for /opt/cmrelay, where the relocated store lives
    relocated = bundle.resolve("/opt/cmrelay/nix/store/aaa-x/bin/x")
    assert relocated == bundle.prefix / "nix/store/aaa-x/bin/x"


def test_an_installed_bundle_resolves_to_itself():
    installed = Bundle(closure="/nix/store/deadbeef-cmrelay-closure")
    assert installed.resolve("/nix/store/aaa/bin/x") == Path("/nix/store/aaa/bin/x")


def test_a_missing_component_names_the_ones_there_are(bundle):
    with pytest.raises(BundleError) as excinfo:
        bundle.bin("nosuchthing")
    assert "postfix" in str(excinfo.value)


def test_a_missing_executable_names_the_keys_there_are(bundle):
    with pytest.raises(BundleError) as excinfo:
        bundle.bin("postfix", "postsuper")
    assert "postconf" in str(excinfo.value)


def test_a_missing_vendored_file_says_which(bundle):
    with pytest.raises(BundleError, match="config/nope.conf"):
        bundle.vendor_file("config/nope.conf")


def test_the_relay_commit_comes_from_the_lock(bundle):
    assert len(bundle.relay_commit) == 40
