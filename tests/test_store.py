"""Publishing the store, and removing the /nix link older releases made.

`unlink_nix` decides whether uninstall removes that symlink or leaves one
dangling into a deleted tree. It takes its roots as parameters precisely so
it can be exercised without a container.
"""

import pytest

from cmrelay import store
from cmrelay.store import StoreError


@pytest.fixture
def roots(tmp_path):
    root, target = tmp_path / "nix", tmp_path / "opt/cmrelay/nix"
    target.mkdir(parents=True)
    return root, target


def test_unlink_nix_removes_only_our_own(roots):
    root, target = roots
    root.symlink_to(target)

    assert store.unlink_nix(root, target) is True
    assert not root.exists()


def test_unlink_nix_leaves_a_foreign_symlink_alone(tmp_path):
    """A rollback must not take away a /nix cmrelay did not put there."""
    root, target = tmp_path / "nix", tmp_path / "opt/cmrelay/nix"
    other = tmp_path / "somewhere/else"
    other.mkdir(parents=True)
    root.symlink_to(other)

    assert store.unlink_nix(root, target) is False
    assert root.readlink() == other


def test_unlink_nix_on_a_host_with_no_nix(tmp_path):
    assert store.unlink_nix(tmp_path / "nix", tmp_path / "target") is False


def test_install_moves_every_staged_path(tmp_path):
    stage = tmp_path / "stage/nix/store"
    for name in ("aaa-one", "bbb-two"):
        (stage / name / "bin").mkdir(parents=True)
        (stage / name / "bin" / "prog").write_text("#!/bin/sh\n")
    target = tmp_path / "opt/cmrelay/nix/store"

    assert store.install(stage, target) == 2
    assert (target / "aaa-one/bin/prog").is_file()
    assert not (stage / "aaa-one").exists()


def test_install_skips_paths_that_are_already_there(tmp_path):
    """Store paths are content-addressed, so re-installing over one is normal."""
    stage = tmp_path / "stage/nix/store"
    (stage / "aaa-one").mkdir(parents=True)
    (stage / "bbb-two").mkdir(parents=True)
    target = tmp_path / "opt/cmrelay/nix/store"
    (target / "aaa-one").mkdir(parents=True)

    assert store.install(stage, target) == 1


def test_install_without_a_staged_store_is_an_error(tmp_path):
    with pytest.raises(StoreError, match="no staged store"):
        store.install(tmp_path / "nowhere", tmp_path / "target")
