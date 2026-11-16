"""The `.run` file: publish the store, link the CLI and stop."""

from .. import CmrelayError, paths
from ..release import store
from ..system import fsops


def installer_verb(args, bundle, out) -> int:
    if bundle.prefix is None:
        raise CmrelayError(
            "this cmrelay is already installed; `cmrelay deploy` is what runs"
            " from here, and `cmrelay upgrade FILE` replaces the release"
        )

    changes = fsops.Changes()
    store.install(changes, bundle, out)
    changes.symlink("cmrelay", paths.LOCAL_BIN / "cmrelay", paths.CLI)
    out.print("\nInstalled; nothing is configured or running yet. Next:")
    if not paths.INI.is_file():
        out.print("\n    cmrelay init chat.example.org")
    out.print("\n    cmrelay deploy")
    return 0
