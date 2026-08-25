"""Render relay's own templates, with relay's own renderer and relay's own Config.

This is the part of cmrelay that could have been a rewrite and deliberately
is not. cmdeploy renders its templates with Jinja2 using a Config object it
imports from chatmaild rather than from cmdeploy, and chatmaild is in the
cmrelay closure. So cmrelay renders the same templates, with the same Config,
from the same chatmail.ini, using the bundled interpreter, and there is no
semantic transformation anywhere in the pipeline.

The Jinja environment matches pyinfra's `files.template` exactly --
StrictUndefined and keep_trailing_newline -- so a template that renders under
cmdeploy renders identically here, and one that would have raised an
undefined-variable error still does.
"""

import os
import shutil
from pathlib import Path

from jinja2 import Environment, StrictUndefined

# Same as pyinfra.api.util.get_template. StrictUndefined is the important
# half: a missing variable must be a loud failure, not a silently empty
# postfix directive.
_ENV = Environment(undefined=StrictUndefined, keep_trailing_newline=True)


def render_template(src: Path, **data) -> str:
    """Render one .j2 file the way pyinfra's files.template would."""
    return _ENV.from_string(Path(src).read_text()).render(**data)


def render_format(src: Path, **params) -> str:
    """Render one .f file the way basedeploy.configure_remote_units would."""
    return Path(src).read_text().format(**params)


def write(dest: Path, content: str | bytes, mode: int = 0o644, owner=None) -> bool:
    """Write a file if its content differs, returning whether it changed.

    Change detection is what drives service restarts, exactly as pyinfra's
    `.changed` does, so it has to be content-based rather than always-write.
    """
    dest = Path(dest)
    data = content.encode() if isinstance(content, str) else content
    dest.parent.mkdir(parents=True, exist_ok=True)

    changed = True
    if dest.exists():
        try:
            changed = dest.read_bytes() != data
        except OSError:
            changed = True

    if changed:
        tmp = dest.with_name(dest.name + ".cmrelay-new")
        tmp.write_bytes(data)
        os.replace(tmp, dest)

    os.chmod(dest, mode)
    if owner:
        shutil.chown(dest, user=owner[0], group=owner[1])
    return changed


def copy(src: Path, dest: Path, mode: int = 0o644, owner=None) -> bool:
    return write(dest, Path(src).read_bytes(), mode=mode, owner=owner)


def ensure_directory(path: Path, mode: int = 0o755, owner=None) -> bool:
    path = Path(path)
    created = not path.exists()
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    if owner:
        shutil.chown(path, user=owner[0], group=owner[1])
    return created


def remove_file(path: Path) -> bool:
    path = Path(path)
    if path.is_file() or path.is_symlink():
        path.unlink()
        return True
    return False


def remove_directory(path: Path) -> bool:
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return True
    return False


class Changes:
    """Tracks whether anything a service depends on was rewritten.

    cmdeploy threads a `need_restart` flag through each deployer for this;
    cmrelay keeps one object per component so the activate step can ask
    "did my configuration change" without every writer remembering to
    propagate a boolean.
    """

    def __init__(self):
        self.changed = set()

    def note(self, component: str, changed: bool) -> bool:
        if changed:
            self.changed.add(component)
        return changed

    def __contains__(self, component: str) -> bool:
        return component in self.changed
