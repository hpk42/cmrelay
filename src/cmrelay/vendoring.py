"""Copy relay's declarative surface into vendor/, and prove it stayed intact.

relay is the source of truth for every template, static config file and
systemd unit a chatmail relay runs on. cmrelay copies them byte for byte
rather than reimplementing them, so the only question worth engineering for
is whether the copy is complete. The mapping in vendor.toml is exhaustive by
construction: `refresh` walks relay's config surface and fails on any file no
rule covers, so upstream adding a component with new templates breaks the
cmrelay build instead of silently producing a relay that is missing a
service.
"""

import ast
import json
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import CmrelayError

# relay pins the binaries cmrelay fetches in a module that imports nothing,
# which is what makes running it to read the tables acceptable.
PINS_SOURCE = "cmdeploy/src/cmdeploy/pins.py"
EXPECTED_PINS = [
    "FILTERMAIL_ARTIFACTS",
    "MTAIL_ARTIFACTS",
    "TURN_ARTIFACTS",
    "IROH_ARTIFACTS",
]


class VendorError(CmrelayError):
    """The mapping and relay's tree no longer agree."""


def globmatch(path: str, pattern: str) -> bool:
    """Match a relay-relative path, with `*` stopping at a directory boundary.

    fnmatch translates `*` to `.*`, which crosses separators, so a rule for
    `dovecot/*` would silently claim `dovecot/conf.d/10-mail.conf` and flatten
    it into the wrong destination: exactly the drift the mapping exists to
    catch. `**` matches across segments and `?` one character.
    """
    expr = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                expr.append(".*")
                index += 2
                continue
            expr.append("[^/]*")
        elif char == "?":
            expr.append("[^/]")
        else:
            expr.append(re.escape(char))
        index += 1
    return re.fullmatch("".join(expr), path) is not None


@dataclass
class Rule:
    src: str
    dest: str


@dataclass
class Skip:
    patterns: list[str]
    keep: list[str] = field(default_factory=list)

    def claims(self, relpath: str) -> bool:
        if any(globmatch(relpath, p) for p in self.keep):
            return False
        return any(globmatch(relpath, p) for p in self.patterns)


@dataclass
class Mapping:
    roots: list[str]
    skips: list[Skip] = field(default_factory=list)
    copies: list[Rule] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Mapping":
        raw = tomllib.loads(Path(path).read_text())
        return cls(
            roots=raw["roots"],
            skips=[Skip(s["patterns"], s.get("keep", [])) for s in raw.get("skip", [])],
            copies=[Rule(c["src"], c["dest"]) for c in raw.get("copy", [])],
        )

    def classify(self, relpath: str) -> tuple[str, str | None]:
        """Return ("skip"|"copy"|"unmatched", destination) for one relay path."""
        for skip in self.skips:
            if skip.claims(relpath):
                return "skip", None
        for rule in self.copies:
            if globmatch(relpath, rule.src):
                if rule.dest.endswith("/"):
                    return "copy", rule.dest + Path(relpath).name
                return "copy", rule.dest
        return "unmatched", None


def walk_surface(relay_root: Path, mapping: Mapping):
    for root in mapping.roots:
        base = relay_root / root
        if not base.is_dir():
            raise VendorError(f"relay root {root!r} does not exist in {relay_root}")
        for path in sorted(base.rglob("*")):
            if path.is_file() or path.is_symlink():
                yield str(path.relative_to(relay_root))


def plan(relay_root: Path, mapping: Mapping) -> dict[str, str]:
    """Resolve the mapping against a relay checkout: relay path -> vendor path.

    Raises VendorError listing every uncovered file, because an upstream
    refactor usually adds several at once.
    """
    result, unmatched = {}, []
    for relpath in walk_surface(relay_root, mapping):
        kind, dest = mapping.classify(relpath)
        if kind == "unmatched":
            unmatched.append(relpath)
        elif kind == "copy":
            result[relpath] = dest
    if unmatched:
        listing = "\n  ".join(unmatched)
        raise VendorError(
            f"{len(unmatched)} relay file(s) match no rule in vendor.toml:\n"
            f"  {listing}\n\n"
            "Decide how cmrelay realises each one, then add a copy or skip rule."
        )

    collisions = {}
    for src, dest in result.items():
        collisions.setdefault(dest, []).append(src)
    clashing = {d: s for d, s in collisions.items() if len(s) > 1}
    if clashing:
        detail = "\n  ".join(f"{d} <- {', '.join(s)}" for d, s in clashing.items())
        raise VendorError(f"vendor destinations collide:\n  {detail}")
    return result


def sync(relay_root: Path, vendor_dir: Path, mapping: Mapping) -> int:
    """Copy the mapped files into an emptied vendor_dir; returns the count."""
    mapped = plan(relay_root, mapping)
    if vendor_dir.exists():
        shutil.rmtree(vendor_dir)
    for src, dest in sorted(mapped.items()):
        target = vendor_dir / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(relay_root / src, target)
    return len(mapped)


def apply_patches(repo: Path, vendor_dir: Path) -> list[str]:
    """Apply patches/series to a freshly synced vendor tree, in order."""
    series = repo / "patches" / "series"
    applied = []
    for line in series.read_text().splitlines():
        name = line.split("#", 1)[0].strip()
        if not name:
            continue
        subprocess.run(
            ["patch", "-p1", "--quiet", "-i", str(repo / "patches" / name)],
            cwd=vendor_dir,
            check=True,
        )
        applied.append(name)
    return applied


def read_pins(path: Path) -> dict:
    """Run relay's pins module and return its artefact tables.

    Running data written as Python is fine; running something that pulls in
    pyinfra is not, so an import anywhere in the module is refused. Tables
    JSON cannot express (DOVECOT_SHA256 is keyed by tuples) are dropped.
    """
    tree = ast.parse(Path(path).read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise VendorError(
                f"{path} imports something at line {node.lineno};"
                " cmrelay only runs it because it does not"
            )
    namespace = {}
    exec(compile(tree, str(path), "exec"), namespace)
    pins = {
        name: value
        for name, value in namespace.items()
        if not name.startswith("__") and _jsonable(value)
    }
    missing = [name for name in EXPECTED_PINS if name not in pins]
    if missing:
        raise VendorError(
            f"{path} no longer defines {', '.join(missing)};"
            " relay changed how it pins the binaries cmrelay fetches"
        )
    return pins


def _jsonable(value) -> bool:
    if isinstance(value, str):
        return True
    if isinstance(value, dict):
        return all(isinstance(k, str) for k in value) and all(
            _jsonable(v) for v in value.values()
        )
    if isinstance(value, (list, tuple)):
        return all(_jsonable(v) for v in value)
    return False


def refresh(repo: Path, relay_root: Path, target: Path) -> int:
    """Write vendor/, vendor.lock and nix/generated/relay-pins.json under target."""
    mapping = Mapping.load(repo / "vendor.toml")
    count = sync(relay_root, target / "vendor", mapping)
    patches = apply_patches(repo, target / "vendor")
    commit = subprocess.run(
        ["git", "-C", str(relay_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    lock = {"relay_commit": commit, "patches": patches}
    (target / "vendor.lock").write_text(json.dumps(lock, indent=2) + "\n")
    pins = read_pins(relay_root / PINS_SOURCE)
    generated = target / "nix" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    text = json.dumps(pins, indent=2, sort_keys=True) + "\n"
    (generated / "relay-pins.json").write_text(text)
    return count
