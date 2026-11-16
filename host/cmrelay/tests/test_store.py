"""Installing a release into the store, and removing what the next one does not use."""

from types import SimpleNamespace

from cmrelay import paths
from cmrelay.release import store
from cmrelay.system import fsops


def staged(fake_root, name, *store_names):
    """A staged closure called *name*, with further store paths beside it."""
    prefix = fake_root / f"stage-{name}"
    (prefix / f"lib/store/{name}-cmrelay-closure/bin").mkdir(parents=True)
    for other in store_names:
        (prefix / "lib/store" / other).mkdir(parents=True)
    return SimpleNamespace(
        prefix=prefix, closure=f"/opt/cmrelay/lib/store/{name}-cmrelay-closure"
    )


def test_a_deploy_after_an_install_keeps_only_what_the_new_release_uses(fake_root, out):
    store.install(fsops.Changes(), staged(fake_root, "old", "shared", "oldlib"), out)
    assert paths.CLI.readlink().parts[-3] == "old-cmrelay-closure"
    store.install(fsops.Changes(), staged(fake_root, "new", "shared"), out)
    assert paths.CLI.readlink().parts[-3] == "new-cmrelay-closure"
    # both releases are on disk until the new one is deployed, so going back is quick
    assert {"old-cmrelay-closure", "oldlib"} <= {p.name for p in paths.STORE.iterdir()}

    store.prune(out)
    assert {p.name for p in paths.STORE.iterdir()} == {"new-cmrelay-closure", "shared"}
