"""The installed version of cmrelay, or a marker that it is not installed."""

from importlib.metadata import version

try:
    __version__ = version("cmrelay")
except Exception:  # noqa: BLE001 - running out of the closure, not a wheel
    __version__ = "0.0.0.dev"
