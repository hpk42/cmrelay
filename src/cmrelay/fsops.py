"""The only module that may touch the filesystem.

Every mutating primitive lives here and returns a `Change` the `Journal`
collects, so a deploy's report is complete by construction; a test walks the
AST of every other module to keep it that way.
"""

import fcntl
import grp
import hashlib
import os
import pwd
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .errors import CmrelayError

ADDED = "+"
CHANGED = "~"
REMOVED = "-"


class LockBusy(CmrelayError):
    """Another cmrelay process holds the lock."""


@contextmanager
def lock(path: Path = paths.LOCK):
    """Hold the deploy lock, or say which process already has it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            holder = handle.read().strip() or "another process"
            raise LockBusy(
                f"another cmrelay run holds {path} (pid {holder});"
                " wait for it to finish, or kill it if it is gone"
            ) from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        yield
    finally:
        handle.close()


@dataclass(frozen=True)
class Change:
    path: Path
    kind: str
    component: str
    system: bool
    detail: str = ""

    def __str__(self):
        line = f"{self.kind} {self.path}"
        return f"{line}  {self.detail}" if self.detail else line


def is_system(path) -> bool:
    """Whether this path is the host's rather than cmrelay's own."""
    path = Path(path)
    return path != paths.ROOT and paths.ROOT not in path.parents


def file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class Journal:
    """What this run changed, and the only way to change anything."""

    changes: list = field(default_factory=list)
    seen: dict = field(default_factory=dict)

    def note(self, change: Change | None) -> Change | None:
        if change is not None:
            self.changes.append(change)
        return change

    def examined(self, component: str) -> None:
        """Count a file this run looked at, changed or not."""
        self.seen[component] = self.seen.get(component, 0) + 1

    @property
    def total_seen(self) -> int:
        return sum(self.seen.values())

    def __contains__(self, component: str) -> bool:
        """Whether anything this component's services read was rewritten."""
        return any(c.component == component for c in self.changes)

    @property
    def system_changes(self) -> list:
        return [c for c in self.changes if c.system]

    @property
    def own_changes(self) -> list:
        return [c for c in self.changes if not c.system]

    @property
    def paths(self) -> set:
        return {c.path for c in self.changes}

    def write(
        self, component: str, dest, content, mode: int = 0o644, owner=None, detail=""
    ) -> Change | None:
        """Write a file if its content differs, and say whether it moved.

        Content-based because this drives service restarts.
        """
        dest = Path(dest)
        data = content.encode() if isinstance(content, str) else content
        existed = dest.exists()
        self.examined(component)
        changed = not (existed and dest.is_file() and dest.read_bytes() == data)
        if changed:
            self._ensure_parents(component, dest.parent)
            self._replace(dest, data)
        changed = self._apply_modes(dest, mode, owner) or changed
        if not changed:
            return None
        kind = CHANGED if existed else ADDED
        return self.note(Change(dest, kind, component, is_system(dest), detail))

    def _replace(self, dest: Path, data: bytes) -> None:
        # through a temporary, so a killed deploy leaves no half file
        tmp = dest.with_name(dest.name + ".cmrelay-new")
        tmp.write_bytes(data)
        os.replace(tmp, dest)

    def _ensure_parents(self, component: str, directory: Path) -> None:
        missing = []
        walk = directory
        while not walk.exists() and walk != walk.parent:
            missing.append(walk)
            walk = walk.parent
        directory.mkdir(parents=True, exist_ok=True)
        for path in reversed(missing):
            self.note(Change(path, ADDED, component, is_system(path)))

    def _apply_modes(self, path: Path, mode, owner) -> bool:
        moved = False
        info = path.stat()
        if mode is not None and (info.st_mode & 0o7777) != mode:
            os.chmod(path, mode)
            moved = True
        if owner:
            want_uid = pwd.getpwnam(owner[0]).pw_uid
            want_gid = grp.getgrnam(owner[1]).gr_gid
            if (info.st_uid, info.st_gid) != (want_uid, want_gid):
                os.chown(path, want_uid, want_gid)
                moved = True
        return moved

    def copy(self, component: str, src, dest, mode: int = 0o644, owner=None):
        return self.write(
            component, dest, Path(src).read_bytes(), mode=mode, owner=owner
        )

    def ensure_directory(
        self, component: str, path, mode: int = 0o755, owner=None, detail: str = ""
    ) -> Change | None:
        path = Path(path)
        created = not path.exists()
        self._ensure_parents(component, path.parent)
        path.mkdir(parents=True, exist_ok=True)
        moved = self._apply_modes(path, mode, owner)
        if not (created or moved):
            return None
        kind = ADDED if created else CHANGED
        return self.note(Change(path, kind, component, is_system(path), detail))

    def remove_file(self, component: str, path, detail: str = "") -> Change | None:
        path = Path(path)
        if not (path.is_file() or path.is_symlink()):
            return None
        path.unlink()
        return self.note(Change(path, REMOVED, component, is_system(path), detail))

    def remove_directory(self, component: str, path, detail: str = "") -> Change | None:
        path = Path(path)
        if not path.is_dir() or path.is_symlink():
            return None
        shutil.rmtree(path)
        return self.note(Change(path, REMOVED, component, is_system(path), detail))

    def symlink(self, component: str, link, target, detail: str = "") -> Change | None:
        link, target = Path(link), Path(target)
        if link.is_symlink() and link.readlink() == target:
            return None
        existed = link.is_symlink() or link.exists()
        self._ensure_parents(component, link.parent)
        if existed:
            link.unlink()
        link.symlink_to(target)
        kind = CHANGED if existed else ADDED
        return self.note(
            Change(link, kind, component, is_system(link), detail or f"-> {target}")
        )

    def chown(self, component: str, path, user: str, group: str) -> Change | None:
        path = Path(path)
        if not path.exists() or not self._apply_modes(path, None, (user, group)):
            return None
        return self.note(
            Change(path, CHANGED, component, is_system(path), f"owner {user}:{group}")
        )

    def chmod(self, component: str, path, mode: int) -> Change | None:
        path = Path(path)
        if not path.exists() or not self._apply_modes(path, mode, None):
            return None
        return self.note(
            Change(path, CHANGED, component, is_system(path), f"mode {mode:o}")
        )

    def install_unit(self, component: str, name: str, content: str, detail: str = ""):
        """Real file in /etc/systemd/system, symlink under /opt/cmrelay/systemd.

        Never the other way round: `systemctl disable` removes a unit that is
        itself a symlink in the search path.
        """
        real = paths.unit_file(name)
        if real.is_symlink():
            # a mask (-> /dev/null), which must go before a real file can
            real.unlink()
        change = self.write(component, real, content, mode=0o644, detail=detail)
        self.symlink(component, paths.UNITS / name, real)
        return change

    def install_dropin(self, component: str, unit: str, name: str, content: str):
        """Drop-ins have to be real files: systemd does not follow a symlinked dir."""
        return self.write(
            component, paths.SYSTEMD_SYSTEM / f"{unit}.d" / name, content, mode=0o644
        )

    def sysctl(self, path, value: str) -> bool:
        """Set a live kernel tunable. Not journalled: the sysctl.d drop-in is."""
        try:
            Path(path).write_text(value)
        except OSError:
            return False
        return True

    def produced(self, component: str, path, detail: str = "") -> Change | None:
        """Record a file another program wrote on cmrelay's behalf."""
        path = Path(path)
        if not path.exists():
            return None
        return self.note(Change(path, CHANGED, component, is_system(path), detail))
