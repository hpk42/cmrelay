"""Finding the closure's own pieces, in staging and once installed."""

import json
import os
import subprocess
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from .. import CmrelayError
from .signature import KEY

# Exported by the installer's header before it hands over.
ENV_STAGE = "CMRELAY_STAGE"
ENV_CLOSURE = "CMRELAY_CLOSURE"


@dataclass
class Bundle:
    """The cmrelay closure, seen either through a staging prefix or directly."""

    closure: Path
    prefix: Path | None = None

    @classmethod
    def discover(cls) -> "Bundle":
        """Locate the closure from the header's environment, or from our own path."""
        closure = os.environ.get(ENV_CLOSURE)
        stage = os.environ.get(ENV_STAGE)
        if closure:
            return cls(Path(closure), Path(stage) if stage else None)

        # Installed: this module lives at <closure>/cmrelay/release/bundle.py
        here = Path(__file__).resolve().parents[2]
        if not (here / "manifest.json").is_file():
            raise CmrelayError(
                f"cannot locate the cmrelay closure from {__file__}; set {ENV_CLOSURE}"
            )
        return cls(here)

    @property
    def root(self) -> Path:
        """Where the closure is right now: staged under a prefix, or in the store."""
        if self.prefix is None:
            return Path(self.closure)
        return self.prefix / "lib/store" / Path(self.closure).name

    @cached_property
    def manifest(self) -> dict:
        path = self.root / "manifest.json"
        try:
            manifest = json.loads(path.read_text())
            manifest["version"], manifest["packages"]
        except (OSError, ValueError, KeyError) as exc:
            raise CmrelayError(f"no usable manifest at {path}: {exc!r}") from exc
        return manifest

    @cached_property
    def packages(self) -> dict:
        return self.manifest["packages"]

    def bin(self, package: str, name: str | None = None) -> str:
        """A package's executable as its *installed* path, not where it is staged."""
        return self.packages[package][name or package]

    def tool(self, name: str) -> str:
        """The installed path of one of the closure's executables, by its name."""
        for entry in self.packages.values():
            if name in entry and name not in ("path", "version"):
                return entry[name]
        raise CmrelayError(f"the closure has no {name!r} executable")

    def run(self, tool, *args, check=True):
        """Run one of the closure's own executables, never one from the host's PATH."""
        argv = [self.tool(tool), *map(str, args)]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise CmrelayError(f"could not run {' '.join(argv)}: {exc}") from exc
        if check and result.returncode:
            raise CmrelayError(
                f"{' '.join(argv)} failed ({result.returncode}):"
                f"\n{result.stderr.strip()}"
            )
        return result

    @cached_property
    def pins(self) -> dict:
        """The pinned binaries, and the chatmail/relay commit of the copied files."""
        return json.loads((self.package / "stack/pins.json").read_text())

    @property
    def relay_commit(self) -> str:
        return self.pins["RELAY_COMMIT"]

    @property
    def package(self) -> Path:
        """The cmrelay package, with the templates, units and pages it reads."""
        return self.root / "cmrelay"

    @property
    def public_key(self) -> Path:
        """The release key this closure verifies an upgrade with."""
        return self.root / KEY
