"""The installed version of cmrelay, or a marker that it is not installed."""

# RHEL 9 and its rebuilds ship Python 3.9 as the system interpreter, and
# a wheel-installed cmrelay only has to parse this far before it execs a
# payload carrying its own 3.14. `X | None` in an annotation is 3.10+ at
# runtime; deferring annotations costs nothing and keeps the pip path
# open on the whole family.
from __future__ import annotations

from importlib.metadata import version

try:
    __version__ = version("cmrelay")
except Exception:  # noqa: BLE001 - running out of the closure, not a wheel
    __version__ = "0.0.0.dev"
