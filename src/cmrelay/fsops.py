"""The only module that may touch the filesystem.

Every mutating primitive lives here and returns a `Change` the `Journal`
collects, so a deploy's report is complete by construction; a test walks the
AST of every other module to keep it that way. `--dry-run` is then a flag on
the journal, and hand-edit detection has one place to look:
`state/written.json` holds the sha256 of every file cmrelay wrote outside its
own tree.
"""

import fcntl
import grp
import hashlib
import os
import pwd
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

ADDED = "+"
CHANGED = "~"
REMOVED = "-"


class LockBusy(Exception):
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


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str | None:
    try:
        return digest(path.read_bytes())
    except OSError:
        return None


@dataclass
class Journal:
    """What this run changed, and the only way to change anything."""

    dry_run: bool = False
    prior: dict = field(default_factory=dict)
    changes: list = field(default_factory=list)
    wrote: dict = field(default_factory=dict)
    hand_edited: list = field(default_factory=list)
    seen: dict = field(default_factory=dict)
    system_seconds: float = 0.0

    # -- reporting ---------------------------------------------------------

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
    def components(self) -> set:
        return {c.component for c in self.changes}

    def for_component(self, component: str) -> list:
        return [c for c in self.changes if c.component == component]

    @property
    def system_changes(self) -> list:
        return [c for c in self.changes if c.system]

    @property
    def own_changes(self) -> list:
        return [c for c in self.changes if not c.system]

    @property
    def paths(self) -> set:
        return {c.path for c in self.changes}

    # -- hand edits --------------------------------------------------------

    def edited_by_hand(self, path: Path, data: bytes) -> bool:
        """Whether somebody other than cmrelay last wrote this file.

        Without a recorded hash there is no evidence either way, so a
        pre-existing file never counts as edited.
        """
        recorded = self.prior.get(str(path))
        if recorded is None:
            return False
        current = file_digest(path)
        if current is None:
            return False
        return current not in (recorded, digest(data))

    def record(self, path: Path, data: bytes) -> None:
        if is_system(path):
            self.wrote[str(path)] = digest(data)

    # -- primitives --------------------------------------------------------

    def write(
        self,
        component: str,
        dest,
        content,
        mode: int = 0o644,
        owner=None,
        detail: str = "",
        guest: bool = False,
    ) -> Change | None:
        """Write a file if its content differs, and say whether it moved.

        Content-based because this drives service restarts. `guest` marks a
        path cmrelay writes but does not own: a hand edit there is left in
        place, where on an owned path the previous content is kept beside it
        as `<file>.replaced`.
        """
        dest = Path(dest)
        data = content.encode() if isinstance(content, str) else content
        existed = dest.exists()
        started = time.monotonic()
        self.examined(component)

        if existed and self.edited_by_hand(dest, data):
            return self.note(self._hand_edit(component, dest, data, mode, guest))

        changed = True
        if existed:
            current = dest.read_bytes() if dest.is_file() else None
            changed = current != data

        if changed and not self.dry_run:
            self._ensure_parents(component, dest.parent)
            self._replace(dest, data)
        if not self.dry_run:
            changed = self._apply_modes(dest, mode, owner) or changed

        self.record(dest, data)
        if is_system(dest):
            self.system_seconds += time.monotonic() - started
        if not changed:
            return None
        kind = CHANGED if existed else ADDED
        return self.note(Change(dest, kind, component, is_system(dest), detail))

    def _hand_edit(self, component, dest, data, mode, guest) -> Change | None:
        if guest:
            self.hand_edited.append((dest, "left alone"))
            return None
        kept = dest.with_name(dest.name + ".replaced")
        self.hand_edited.append((dest, f"overwritten, previous kept as {kept.name}"))
        if not self.dry_run:
            shutil.copyfile(dest, kept)
            self._replace(dest, data)
            self._apply_modes(dest, mode, None)
        self.record(dest, data)
        return Change(dest, CHANGED, component, is_system(dest), "was edited by hand")

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

    def copy(self, component: str, src, dest, mode: int = 0o644, owner=None, **kw):
        return self.write(
            component, dest, Path(src).read_bytes(), mode=mode, owner=owner, **kw
        )

    def ensure_directory(
        self, component: str, path, mode: int = 0o755, owner=None, detail: str = ""
    ) -> Change | None:
        path = Path(path)
        created = not path.exists()
        if self.dry_run:
            return (
                self.note(Change(path, ADDED, component, is_system(path), detail))
                if created
                else None
            )
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
        if not self.dry_run:
            path.unlink()
        self.wrote.pop(str(path), None)
        return self.note(Change(path, REMOVED, component, is_system(path), detail))

    def remove_directory(self, component: str, path, detail: str = "") -> Change | None:
        path = Path(path)
        if not path.is_dir() or path.is_symlink():
            return None
        if not self.dry_run:
            shutil.rmtree(path)
        return self.note(Change(path, REMOVED, component, is_system(path), detail))

    def symlink(self, component: str, link, target, detail: str = "") -> Change | None:
        link, target = Path(link), Path(target)
        if link.is_symlink() and link.readlink() == target:
            return None
        existed = link.is_symlink() or link.exists()
        if not self.dry_run:
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
        if self.dry_run or not path.exists():
            return None
        if not self._apply_modes(path, None, (user, group)):
            return None
        return self.note(
            Change(path, CHANGED, component, is_system(path), f"owner {user}:{group}")
        )

    def chmod(self, component: str, path, mode: int) -> Change | None:
        path = Path(path)
        if self.dry_run or not path.exists():
            return None
        if not self._apply_modes(path, mode, None):
            return None
        return self.note(
            Change(path, CHANGED, component, is_system(path), f"mode {mode:o}")
        )

    # -- systemd units -----------------------------------------------------

    def install_unit(self, component: str, name: str, content: str, detail: str = ""):
        """Real file in /etc/systemd/system, symlink under /opt/cmrelay/systemd.

        Never the other way round: `systemctl disable` removes a unit that is
        itself a symlink in the search path.
        """
        real = paths.unit_file(name)
        if real.is_symlink() and not self.dry_run:
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

    def remove_dropin(self, component: str, unit: str, name: str):
        return self.remove_file(component, paths.SYSTEMD_SYSTEM / f"{unit}.d" / name)

    def sysctl(self, path, value: str) -> bool:
        """Set a live kernel tunable. Not journalled: the sysctl.d drop-in is."""
        if self.dry_run:
            return True
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
