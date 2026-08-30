"""Generations, the migration selector, and release discovery."""

import json
from pathlib import Path

import pytest

from cmrelay import discovery, fsops, generations, migrations, paths
from cmrelay.output import Out

# -- generations -----------------------------------------------------------


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


def test_a_generation_link_is_relative_so_the_tree_can_move(fake_root):
    journal = fsops.Journal()
    generations.create(journal, 1, closure(fake_root, "aaa"))
    assert str((paths.GENERATIONS / "1").readlink()).startswith("../nix/store/")


def test_rollback_repoints_current_without_removing_anything(fake_root):
    journal = fsops.Journal()
    for number, name in ((1, "aaa"), (2, "bbb")):
        generations.create(journal, number, closure(fake_root, name))
    generations.flip(journal, 2)
    assert generations.current() == 2

    generations.flip(journal, 1)
    assert generations.current() == 1
    assert generations.existing() == [1, 2]


def test_state_is_snapshotted_and_restored(fake_root):
    journal = fsops.Journal()
    journal.write("x", paths.INI, "[params]\nmail_domain = a.example.org\n")
    journal.write("x", paths.WWW_SRC / "index.md", "# mine\n")
    generations.snapshot_state(journal, 1)

    journal.write("x", paths.INI, "[params]\nmail_domain = b.example.org\n")
    assert generations.restore_state(journal, 1)
    assert "a.example.org" in paths.INI.read_text()
    assert (paths.WWW_SRC / "index.md").read_text() == "# mine\n"


def test_pruning_keeps_the_current_and_one_previous(fake_root, out):
    journal = fsops.Journal()
    for number in (1, 2, 3, 4):
        path = closure(fake_root, f"gen{number}")
        generations.create(journal, number, path, [path.name])
    generations.flip(journal, 4)

    removed = generations.prune(journal, out)
    assert sorted(removed) == [1, 2]
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


# -- the migration selector -----------------------------------------------


def fake_migration(version, name):
    return migrations.Migration(version, name, "moves a path", lambda j, o: None)


@pytest.fixture
def three(monkeypatch):
    listed = [
        fake_migration("0.4.0", "move-www"),
        fake_migration("0.5.0", "rename-unit"),
        fake_migration("0.6.0", "state-format"),
    ]
    monkeypatch.setattr(migrations, "MIGRATIONS", listed)
    return listed


def test_only_migrations_strictly_newer_than_installed_apply(three):
    chosen = migrations.pending("0.4.0", "0.6.0")
    assert [m.name for m in chosen] == ["rename-unit", "state-format"]


def test_a_migration_at_the_candidate_version_applies(three):
    assert [m.name for m in migrations.pending("0.3.0", "0.4.0")] == ["move-www"]


def test_an_already_applied_migration_is_skipped(three):
    chosen = migrations.pending("0.3.0", "0.6.0", done={"move-www", "rename-unit"})
    assert [m.name for m in chosen] == ["state-format"]


def test_no_migrations_between_adjacent_releases(three):
    assert migrations.pending("0.6.0", "0.6.1") == []


def test_an_unparseable_version_selects_nothing(three):
    """Guessing an order between two names that are not versions is worse."""
    assert migrations.pending("0.0.0.dev", "not-a-version") == []
    assert migrations.pending("dev", "0.6.0") == []


def test_migrations_run_in_version_order(three, out):
    ran = []
    listed = [
        migrations.Migration("0.6.0", "third", "x", lambda j, o: ran.append("third")),
        migrations.Migration("0.4.0", "first", "x", lambda j, o: ran.append("first")),
        migrations.Migration("0.5.0", "second", "x", lambda j, o: ran.append("second")),
    ]
    applied = migrations.apply(
        sorted(listed, key=lambda m: m.version), fsops.Journal(), out
    )
    assert ran == ["first", "second", "third"]
    assert applied == ["first", "second", "third"]


# -- discovery -------------------------------------------------------------


def github_release(version, arch="x86_64", signed=True):
    name = f"cmrelay-installer-{version}-{arch}-linux.run"
    assets = [
        {"name": name, "browser_download_url": f"https://gh/{name}", "size": 64 * 2**20}
    ]
    if signed:
        assets.append(
            {"name": f"{name}.sig", "browser_download_url": f"https://gh/{name}.sig"}
        )
    return {"draft": False, "prerelease": False, "assets": assets}


def pypi_body(versions, arch="x86_64"):
    releases = {}
    for version in versions:
        name = f"cmrelay-{version}-py3-none-manylinux_2_17_{arch}.whl"
        releases[version] = [
            {"filename": name, "url": f"https://pypi/{name}", "size": 64 * 2**20}
        ]
    return {"releases": releases}


@pytest.fixture
def answers(monkeypatch):
    """Route both endpoints to canned bodies, keyed by url prefix."""
    canned = {}

    def fetch(url, ctx, timeout=None):
        for prefix, body in canned.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return json.dumps(body).encode()
        raise AssertionError(url)

    monkeypatch.setattr(discovery, "fetch", fetch)
    monkeypatch.setattr(discovery, "context", lambda ca: None)
    monkeypatch.setattr(discovery, "arch", lambda: "x86_64")
    return canned


def test_github_newer_wins(answers):
    answers[discovery.GITHUB] = [github_release("0.5.0")]
    answers[discovery.PYPI] = pypi_body(["0.4.2"])
    results, failures = discovery.survey("/etc/ssl/certs")
    assert not failures
    assert discovery.best(results.values()).source == "GitHub"
    assert discovery.best(results.values()).version == "0.5.0"


def test_pypi_newer_wins(answers):
    answers[discovery.GITHUB] = [github_release("0.4.0")]
    answers[discovery.PYPI] = pypi_body(["0.5.1"])
    results, _ = discovery.survey("/etc/ssl/certs")
    assert discovery.best(results.values()).source == "PyPI"


def test_one_source_down_leaves_the_other_usable(answers):
    answers[discovery.GITHUB] = OSError("no route to host")
    answers[discovery.PYPI] = pypi_body(["0.5.1"])
    results, failures = discovery.survey("/etc/ssl/certs")
    assert list(results) == ["PyPI"]
    assert "GitHub" in failures


def test_both_down_yields_no_candidate(answers):
    answers[discovery.GITHUB] = OSError("down")
    answers[discovery.PYPI] = OSError("down")
    results, failures = discovery.survey("/etc/ssl/certs")
    assert results == {}
    assert sorted(failures) == ["GitHub", "PyPI"]


def test_a_release_with_no_signature_beside_it_is_not_offered(answers):
    answers[discovery.GITHUB] = [github_release("0.9.0", signed=False)]
    answers[discovery.PYPI] = pypi_body([])
    results, _ = discovery.survey("/etc/ssl/certs", source="github")
    assert results == {}


def test_a_release_for_another_architecture_is_not_offered(answers):
    answers[discovery.GITHUB] = [github_release("0.9.0", arch="aarch64")]
    results, _ = discovery.survey("/etc/ssl/certs", source="github")
    assert results == {}


def test_pinning_a_source_asks_only_that_one(answers):
    answers[discovery.GITHUB] = [github_release("0.5.0")]
    results, _ = discovery.survey("/etc/ssl/certs", source="github")
    assert list(results) == ["GitHub"]


def test_a_wheel_without_a_signature_inside_is_refused(tmp_path):
    import zipfile

    archive = tmp_path / "w.whl"
    with zipfile.ZipFile(archive, "w") as wheel:
        wheel.writestr(discovery.WHEEL_PAYLOAD, "#!/bin/sh\n")
    with pytest.raises(discovery.DiscoveryError, match="cmrelay.run.sig"):
        discovery.unpack_wheel(archive.read_bytes(), tmp_path / "out", fsops.Journal())


def test_a_host_with_no_ca_bundle_says_to_download_by_hand(tmp_path):
    with pytest.raises(discovery.DiscoveryError, match="by hand"):
        discovery.context(str(tmp_path / "absent"))


def test_the_report_names_the_laggard(answers, capsys):
    answers[discovery.GITHUB] = [github_release("0.5.0")]
    answers[discovery.PYPI] = pypi_body(["0.4.2"])
    results, failures = discovery.survey("/etc/ssl/certs")
    discovery.report(Out(colour=False), "0.3.0", results, failures)
    printed = capsys.readouterr().out
    assert "0.5.0" in printed
    assert "0.4.2" in printed
    assert Path("/etc/ssl/certs").name not in printed
