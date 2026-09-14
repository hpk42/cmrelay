"""Which cmrelay release this is, from the closure's manifest.

Without a version there is no "is this candidate newer" and no generation
label, so `upgrade` cannot exist. A checkout has no manifest and reports DEV.
"""

from packaging.version import InvalidVersion, Version

from .bundle import Bundle, BundleError

DEV = "0.0.0.dev"


def parse_version(text):
    """A PEP 440 version, or None for anything that is not one."""
    try:
        return Version(str(text))
    except (InvalidVersion, TypeError):
        return None


try:
    __version__ = Bundle.discover().manifest.get("version") or DEV
except (BundleError, ValueError):
    __version__ = DEV
