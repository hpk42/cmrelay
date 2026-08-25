"""Finding the payload's own pieces, in staging and once installed.

The installer runs twice from two different roots. The first time it runs out
of a staging directory that the stub extracted to, before anything has
touched the host; the second time it runs out of the installed store, after
the closure has been moved into place. Both times it needs the same four
things: the component manifest, the vendored relay configuration, the units
cmrelay authors, and vendor.lock.

`prefix` is what separates the two. In staging every store path has the
staging root in front of it; once installed the prefix is empty and the paths
are what the binaries themselves were built for.
"""

import json
import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

# The stub exports these before handing over.
ENV_STAGE = "CMRELAY_STAGE"
ENV_CLOSURE = "CMRELAY_CLOSURE"

# The stub writes these into the closure; see nix/closure.nix.
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

        Unit files and rendered configuration must name the path the binary
        will have once the store is in place, never the staging path, so this
        deliberately does not go through resolve().
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

        Installed, that is just the binary. In staging it cannot be: the
        binary names an absolute /nix/store path as its ELF interpreter and
        that path does not exist yet. Invoking ld.so with the binary as an
        argument bypasses PT_INTERP, and --library-path outranks the
        DT_RUNPATH nix writes, which is the same trick the stub uses to start
        the interpreter in the first place.

        This matters most for the preflight: without it, every store binary
        it wants to run would simply fail, and a detector that cannot run
        looks exactly like a detector that found nothing.
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
