"""Finding the payload's own pieces, in staging and once installed.

The installer runs once out of the stub's staging directory and again out of
the installed store, needing the same manifest, vendored configuration, units
and vendor.lock both times. `prefix` separates them: in staging every store
path has the staging root in front of it, and once installed it is empty.
"""

# Deferred so `X | None` parses on the 3.9 that RHEL 9 ships.
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

# Exported by the stub before it hands over.
ENV_STAGE = "CMRELAY_STAGE"
ENV_CLOSURE = "CMRELAY_CLOSURE"

# How the stub tells a complete staging tree from a consumed one. Shared with
# stub/header.sh.
READY_MARKER = ".cmrelay-ready"

# Written into the closure by nix/closure.nix.
BOOTSTRAP = "bootstrap.env"


class BundleError(Exception):
    """The payload is not laid out the way the installer expects."""


@dataclass
class Bundle:
    """The cmrelay closure, seen either through a staging prefix or directly."""

    closure: Path
    prefix: Path | None = None

    @classmethod
    def discover(cls) -> "Bundle":
        """Locate the bundle from the stub's environment, or from our own path."""
        closure = os.environ.get(ENV_CLOSURE)
        stage = os.environ.get(ENV_STAGE)
        if closure:
            return cls(Path(closure), Path(stage) if stage else None)

        # Installed: this module lives at <closure>/cmrelay/bundle.py
        here = Path(__file__).resolve().parent.parent
        if not (here / "manifest.json").is_file():
            raise BundleError(
                f"cannot locate the cmrelay closure from {__file__}; set {ENV_CLOSURE}"
            )
        return cls(here)

    def resolve(self, store_path) -> Path:
        """Map a store path to where it actually is right now."""
        if self.prefix is None:
            return Path(store_path)
        return self.prefix / str(store_path).lstrip("/")

    @property
    def root(self) -> Path:
        return self.resolve(self.closure)

    @cached_property
    def manifest(self) -> dict:
        path = self.root / "manifest.json"
        try:
            return json.loads(path.read_text())
        except OSError as exc:
            raise BundleError(f"no manifest at {path}: {exc}") from exc

    @cached_property
    def components(self) -> dict:
        return self.manifest["components"]

    def component(self, name: str) -> dict:
        try:
            return self.components[name]
        except KeyError:
            raise BundleError(
                f"the closure has no {name!r} component;"
                f" it has {', '.join(sorted(self.components))}"
            ) from None

    def bin(self, component: str, name: str | None = None) -> str:
        """A component's executable, as the *installed* path.

        Units and rendered configuration must name the path the binary will
        have once installed, so this deliberately skips resolve().
        """
        entry = self.component(component)
        key = name or component
        try:
            return entry[key]
        except KeyError:
            raise BundleError(
                f"{component} does not provide {key!r};"
                f" it has {', '.join(k for k in entry if k != 'version')}"
            ) from None

    def version(self, component: str) -> str:
        return self.component(component).get("version", "unknown")

    @cached_property
    def _bootstrap(self) -> dict:
        path = self.root / BOOTSTRAP
        values = {}
        for line in path.read_text().splitlines():
            key, _, value = line.strip().partition("=")
            if key:
                values[key] = value
        return values

    def command(self, component: str, name: str | None = None) -> list[str]:
        """An argv that runs one of the closure's binaries right now.

        In staging the binary's ELF interpreter does not exist yet, so this
        goes through ld.so, the same trick the stub uses. It matters most for
        the preflight, where a detector that cannot run would look exactly
        like one that found nothing.
        """
        binary = self.resolve(self.bin(component, name))
        if self.prefix is None:
            return [str(binary)]
        loader = self.resolve(self._bootstrap["CMRELAY_LD"])
        libs = ":".join(
            str(p) for p in sorted((self.prefix / "nix/store").glob("*/lib"))
        )
        return [str(loader), "--library-path", libs, str(binary)]

    @cached_property
    def lock(self) -> dict:
        return json.loads((self.root / "vendor.lock").read_text())

    @property
    def relay_commit(self) -> str:
        return self.lock["relay_commit"]

    @property
    def vendor(self) -> Path:
        return self.root / "vendor"

    @property
    def units(self) -> Path:
        """The units cmrelay authors, for daemons nixpkgs ships none for."""
        return self.root / "units"

    def vendor_file(self, relpath: str) -> Path:
        path = self.vendor / relpath
        if not path.is_file():
            raise BundleError(f"vendored file missing from the closure: {relpath}")
        return path
