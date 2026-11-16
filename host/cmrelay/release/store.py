"""The store: the installed closure's paths, and nothing else once a deploy worked.

Its place is fixed, since every binary names its libraries by their path in it.
"""

import os
import shutil
from pathlib import Path

from .. import paths
from ..system import fsops


def install(changes, bundle, out) -> None:
    """Move the staged store paths into place and make the CLI the staged closure's."""
    stage = bundle.prefix / "lib/store"
    names = sorted(p.name for p in stage.iterdir())
    changes.ensure_directory("cmrelay", paths.ROOT)
    paths.STORE.mkdir(parents=True, exist_ok=True)
    moved = 0
    for name in names:
        dest = paths.STORE / name
        # content-addressed, so an existing path is the same bytes
        if dest.exists():
            continue
        # staged under lib/.staging, so on the same filesystem: each path appears whole
        os.rename(stage / name, dest)
        moved += 1
    out.print(f"  {moved} new store paths under {paths.STORE}")
    changes.write("cmrelay", paths.STORE_PATHS, "\n".join(names) + "\n")
    # relative, so the whole tree can be moved
    cli = Path("../store") / Path(bundle.closure).name / "bin/cmrelay"
    changes.symlink("cmrelay", paths.CLI, cli)


def prune(out) -> None:
    """Remove the store paths the installed closure does not use."""
    keep = set(fsops.read_lines(paths.STORE_PATHS))
    if not keep:
        return
    gone = [p for p in sorted(paths.STORE.iterdir()) if p.name not in keep]
    for path in gone:
        shutil.rmtree(path)
    if gone:
        out.detail(
            f"removed {len(gone)} store paths the installed release does not use"
        )
