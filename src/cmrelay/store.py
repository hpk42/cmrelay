"""Move the closure into place, and make /nix resolve to it.

See cmrelay.paths for why the store keeps its paths and only the bytes move.
"""

import os
import shutil
import subprocess
from pathlib import Path

from . import paths


class StoreError(Exception):
    pass


def install(stage_store: Path, target: Path = paths.STORE) -> int:
    """Move every staged store path under *target*. Returns how many moved.

    A rename is instant when staging and /opt share a filesystem, which is
    why the stub defaults staging to /var/tmp rather than /tmp. It falls back
    to a copy when they do not, rather than failing.
    """
    stage_store = Path(stage_store)
    if not stage_store.is_dir():
        raise StoreError(f"no staged store at {stage_store}")

    target.mkdir(parents=True, exist_ok=True)
    moved = 0
    for entry in sorted(stage_store.iterdir()):
        dest = target / entry.name
        if dest.exists():
            # Content-addressed, so an existing path is the same bytes.
            continue
        try:
            os.rename(entry, dest)
        except OSError:
            shutil.copytree(entry, dest, symlinks=True)
        moved += 1
    return moved


def link_nix(root: Path = paths.NIX_ROOT, target: Path = paths.NIX) -> None:
    """Point /nix at cmrelay's tree, refusing to disturb a real store."""
    if root.is_symlink():
        if root.readlink() != target and root.resolve() != target:
            raise StoreError(f"{root} already points at {root.resolve()}")
        return
    if root.exists():
        raise StoreError(
            f"{root} exists as a real directory: this host already runs Nix."
            " cmrelay will not mingle closures."
        )
    root.symlink_to(target)


def unlink_nix(root: Path = paths.NIX_ROOT, target: Path = paths.NIX) -> bool:
    """Remove the symlink, and only if it is ours."""
    if root.is_symlink() and (root.readlink() == target or root.resolve() == target):
        root.unlink()
        return True
    return False


def label_for_selinux(target: Path = paths.NIX) -> str | None:
    """Label the store so systemd will execute out of it.

    Binaries unpacked into an unlabelled directory are what the default
    RHEL-family policy exists to stop. Returns a message when labelling was
    attempted, so the caller can report it rather than have it happen
    invisibly.
    """
    if not shutil.which("getenforce"):
        return None
    try:
        status = subprocess.run(
            ["getenforce"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if status.stdout.strip() != "Enforcing":
        return None
    chcon = shutil.which("chcon")
    if not chcon:
        return (
            "SELinux is enforcing but chcon is missing;"
            " services will likely fail to start out of the store"
        )
    result = subprocess.run(
        [chcon, "-R", "-t", "bin_t", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return f"SELinux relabel of {target} failed: {result.stderr.strip()}"
    return f"SELinux is enforcing; relabelled {target} as bin_t"
