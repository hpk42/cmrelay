"""How a deploy touches the filesystem, recording each change and its component."""

import fcntl
import grp
import hashlib
import os
import pwd
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .. import CmrelayError, paths


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
            raise CmrelayError(
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


@contextmanager
def scratch(path: Path):
    """A private directory, empty on entry and gone on exit, however that comes."""
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, mode=0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@dataclass(frozen=True)
class Change:
    path: Path
    component: str


def file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_lines(path: Path) -> list:
    """The entries of a state file under /opt/cmrelay/lib/state, or none."""
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


@dataclass
class Changes:
    """What this run changed."""

    made: list = field(default_factory=list)
    # the first line of every file it marks; with the version on a deploy
    marker: str = paths.MARKER
    # every component counts as changed, after a deploy that did not finish
    redo: bool = False

    def note(self, path: Path, component: str) -> None:
        self.made.append(Change(path, component))

    def __contains__(self, component: str) -> bool:
        """Whether anything this component's services read was rewritten."""
        return self.redo or any(c.component == component for c in self.made)

    def __iter__(self):
        return iter(self.made)

    def __len__(self) -> int:
        return len(self.made)

    def write(self, component: str, dest, content, mode: int = 0o644, owner=None):
        """Write a file if its content differs: content decides, since this drives restarts."""
        dest = Path(dest)
        data = content.encode() if isinstance(content, str) else content
        same = dest.is_file() and not dest.is_symlink() and dest.read_bytes() == data
        if not same:
            dest.parent.mkdir(parents=True, exist_ok=True)
            self._replace(dest, data, mode, owner)
        else:
            # a link swapped in meanwhile cannot redirect the descriptor
            fd = os.open(dest, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                same = not self._apply_modes(fd, mode, owner)
            finally:
                os.close(fd)
        if not same:
            self.note(dest, component)

    def _replace(self, dest: Path, data: bytes, mode, owner) -> None:
        # Through a fresh temporary, with mode and owner set before the rename:
        # /etc/dkimkeys belongs to opendkim, which could plant a link there.
        tmp = dest.with_name(dest.name + ".cmrelay-new")
        tmp.unlink(missing_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with os.fdopen(os.open(tmp, flags, 0o600), "wb") as f:
            f.write(data)
            self._apply_modes(f.fileno(), mode, owner)
        os.replace(tmp, dest)

    def _apply_modes(self, path, mode, owner) -> bool:
        """For a path or an open descriptor, which no swapped-in link redirects."""
        moved = False
        info = os.stat(path)
        if (info.st_mode & 0o7777) != mode:
            os.chmod(path, mode)
            moved = True
        if owner:
            want_uid = pwd.getpwnam(owner[0]).pw_uid
            want_gid = grp.getgrnam(owner[1]).gr_gid
            if (info.st_uid, info.st_gid) != (want_uid, want_gid):
                os.chown(path, want_uid, want_gid)
                moved = True
        return moved

    def add_lines(self, path: Path, names) -> None:
        """Add to what a state file already lists, never replacing it."""
        merged = sorted(set(read_lines(path)) | set(names))
        self.write("cmrelay", path, "\n".join(merged) + "\n")

    def copy(self, component: str, src, dest, mode: int = 0o644) -> None:
        self.write(component, dest, Path(src).read_bytes(), mode=mode)

    def ensure_directory(self, component: str, path, mode: int = 0o755, owner=None):
        path = Path(path)
        created = not path.exists()
        path.mkdir(parents=True, exist_ok=True)
        if self._apply_modes(path, mode, owner) or created:
            self.note(path, component)

    def remove_file(self, component: str, path) -> None:
        path = Path(path)
        if path.is_file() or path.is_symlink():
            path.unlink()
            self.note(path, component)

    def symlink(self, component: str, link, target) -> None:
        link, target = Path(link), Path(target)
        if link.is_symlink() and link.readlink() == target:
            return
        link.parent.mkdir(parents=True, exist_ok=True)
        # through a temporary, so there is no moment without the link
        tmp = link.with_name(link.name + ".cmrelay-new")
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(target)
        os.replace(tmp, link)
        self.note(link, component)

    def marked(self, text: str) -> str:
        """The text with the marker line, after the shebang of a script."""
        if text.startswith("#!"):
            shebang, text = text.split("\n", 1)
            return f"{shebang}\n{self.marker}\n{text}"
        return f"{self.marker}\n{text}"

    def install_unit(self, component: str, name: str, content: str) -> None:
        """A real file: `systemctl disable` removes a unit that is a symlink."""
        real = paths.SYSTEMD_SYSTEM / name
        if real.is_symlink():
            # a mask (-> /dev/null) must go before a real file can
            real.unlink()
        self.write(component, real, self.marked(content))

    def install_dropin(self, component: str, unit: str, name: str, content: str):
        """Drop-ins have to be real files: systemd does not follow a symlinked dir."""
        dest = paths.SYSTEMD_SYSTEM / f"{unit}.d" / name
        self.write(component, dest, self.marked(content))
