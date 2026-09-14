"""Finding the closure's own pieces, in staging and once installed.

The installer runs out of the stub's staging directory, the CLI out of the
installed store, and both need the same manifest, vendored configuration,
units and vendor.lock. `prefix` separates them: in staging every store path
has the staging root in front of it, and once installed it is empty.
"""

import json
import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from .errors import CmrelayError

# Exported by the stub before it hands over.
ENV_STAGE = "CMRELAY_STAGE"
ENV_CLOSURE = "CMRELAY_CLOSURE"

# How the stub tells a complete staging tree from a consumed one. Shared with
# stub/header.sh.
READY_MARKER = ".cmrelay-ready"


class BundleError(CmrelayError):
    """The closure is not laid out the way cmrelay expects."""


@dataclass
class Bundle:
    """The cmrelay closure, seen either through a staging prefix or directly."""

    closure: Path
    prefix: Path | None = None

    @classmethod
    def discover(cls) -> "Bundle":
        """Locate the closure from the stub's environment, or from our own path."""
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
