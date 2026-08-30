"""Which cmrelay release this is.

The closure's manifest is the answer on an installed host; importlib.metadata
answers for a pip-installed wheel that has not handed over yet. Without a
version there is no "is this candidate newer", no migration range and no
generation label, so `upgrade` cannot exist.
"""

# Deferred so `X | None` parses on the 3.9 that RHEL 9 ships.
from __future__ import annotations

import json
import os
from importlib.metadata import version as metadata_version
from pathlib import Path

DEV = "0.0.0.dev"


def parse_version(text):
    """A PEP 440 version, or None for anything that is not one.

    packaging is imported here rather than at module scope: `cmrelay` has to
    import under a bare interpreter, both from the dependency-free wheel and
    from the build scripts, and only `upgrade` orders versions.
    """
    from packaging.version import InvalidVersion, Version

    try:
        return Version(str(text))
    except (InvalidVersion, TypeError):
        return None


def from_bundle() -> tuple:
    """(version, commit) out of the closure's manifest, if there is one."""
    closure = os.environ.get("CMRELAY_CLOSURE")
    stage = os.environ.get("CMRELAY_STAGE")
    if closure:
        root = Path(stage) / str(closure).lstrip("/") if stage else Path(closure)
    else:
        root = Path(__file__).resolve().parent.parent
    try:
        manifest = json.loads((root / "manifest.json").read_text())
    except (OSError, ValueError):
        return None, None
    return manifest.get("version"), manifest.get("commit")


def from_metadata() -> str | None:
    try:
        return metadata_version("cmrelay")
    except Exception:  # noqa: BLE001 - running out of the closure, not a wheel
        return None


__version__, __commit__ = from_bundle()
if not __version__:
    __version__ = from_metadata() or DEV
