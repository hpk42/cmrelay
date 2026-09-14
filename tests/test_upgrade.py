"""Generations and release discovery."""

import json

import pytest

from cmrelay import discovery, fsops, generations, paths, upgrade


def closure(fake_root, name):
    path = paths.STORE / f"{name}-cmrelay-closure"
    (path / "bin").mkdir(parents=True)
    (path / "bin/cmrelay").write_text("#!/bin/sh\n")
    return path


def test_the_first_generation_is_one_and_current_points_at_it(fake_root):
    journal = fsops.Journal()
    generations.create(journal, 1, closure(fake_root, "aaa"), ["aaa-cmrelay-closure"])
    generations.flip(journal, 1)

    assert generations.existing() == [1]
    assert generations.current() == 1
    assert paths.CLI.resolve().name == "cmrelay"
    assert str((paths.GENERATIONS / "1").readlink()).startswith("../nix/store/")


def test_rollback_repoints_current_without_removing_anything(fake_root):
    journal = fsops.Journal()
    for number, name in ((1, "aaa"), (2, "bbb")):
        generations.create(journal, number, closure(fake_root, name), [])
    generations.flip(journal, 2)
    assert generations.current() == 2

    generations.flip(journal, 1)
    assert generations.current() == 1
    assert generations.existing() == [1, 2]


def test_pruning_keeps_the_current_and_one_previous(fake_root, out):
    journal = fsops.Journal()
    for number in (1, 2, 3, 4):
        path = closure(fake_root, f"gen{number}")
        generations.create(journal, number, path, [path.name])
    generations.flip(journal, 4)

    assert sorted(generations.prune(journal, out)) == [1, 2]
    assert generations.existing() == [3, 4]
    assert not (paths.STORE / "gen1-cmrelay-closure").exists()
    assert (paths.STORE / "gen4-cmrelay-closure").exists()


def test_pruning_never_removes_a_store_path_a_kept_generation_shares(fake_root, out):
    journal = fsops.Journal()
    shared = "shared-thing"
    (paths.STORE / shared).mkdir(parents=True)
    for number in (1, 2, 3):
        path = closure(fake_root, f"gen{number}")
        generations.create(journal, number, path, [path.name, shared])
    generations.flip(journal, 3)

    generations.prune(journal, out)
    assert (paths.STORE / shared).exists()


def test_only_a_newer_candidate_is_installed_without_force(monkeypatch):
    monkeypatch.setattr(upgrade, "__version__", "0.4.0")
    assert upgrade.newer("0.5.0", False)
    assert not upgrade.newer("0.4.0", False)
    assert not upgrade.newer("not-a-version", False)
    assert upgrade.newer("0.3.0", True)


# -- discovery -------------------------------------------------------------


def github_release(version, arch=discovery.arch(), signed=True):
    name = f"cmrelay-installer-{version}-{arch}-linux.run"
    assets = [
        {"name": name, "browser_download_url": f"https://gh/{name}", "size": 64 * 2**20}
    ]
    if signed:
        assets.append(
            {"name": f"{name}.sig", "browser_download_url": f"https://gh/{name}.sig"}
        )
    return {"draft": False, "prerelease": False, "assets": assets}


def canned(body):
    return lambda url, ctx, timeout=None: json.dumps(body).encode()


def test_the_newest_signed_release_for_this_architecture_wins():
    other = "aarch64" if discovery.arch() == "x86_64" else "x86_64"
    found = discovery.releases(
        None,
        fetch=canned(
            [
                github_release("0.4.0"),
                github_release("0.5.0"),
                github_release("0.9.0", signed=False),
                github_release("0.8.0", arch=other),
            ]
        ),
    )
    assert [c.version for c in found] == ["0.4.0", "0.5.0"]
    assert discovery.newest(found).version == "0.5.0"
    assert discovery.newest([]) is None


def test_a_host_with_no_ca_bundle_says_to_download_by_hand(tmp_path):
    with pytest.raises(discovery.DiscoveryError, match="by hand"):
        discovery.context(str(tmp_path / "absent"))
