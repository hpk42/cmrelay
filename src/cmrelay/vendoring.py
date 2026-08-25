"""Copy relay's declarative surface into vendor/, and prove it stayed intact.

relay is the source of truth for every template, static config file and
systemd unit a chatmail relay runs on. cmrelay copies them byte for byte
rather than reimplementing them, so the only question worth engineering for
is whether the copy is complete.

The mapping in vendor.toml is exhaustive by construction: sync walks relay's
config surface and fails on any file no rule covers. Upstream adding a
component with new templates therefore breaks the cmrelay build, instead of
silently producing a relay that is missing a service.

vendor.lock records the relay commit plus a sha256 per copied file, which is
what makes a cmrelay tag name an exact relay tree.
"""

import hashlib
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class VendorError(Exception):
    """The mapping and relay's tree no longer agree."""


def globmatch(path: str, pattern: str) -> bool:
    """Match a relay-relative path, with `*` stopping at a directory boundary.

    Written out rather than built on fnmatch, for two reasons. fnmatch
    translates `*` to `.*`, which crosses separators, so a rule for
    `dovecot/*` would silently claim `dovecot/conf.d/10-mail.conf` and
    flatten it into the wrong destination: exactly the drift the mapping
    exists to catch. And post-processing `fnmatch.translate`'s output means
    depending on its shape, which changed in Python 3.14 (a lowercase
    end-of-string anchor) and broke this on the interpreter the closure
    actually ships.

    `*` matches within one path segment, `**` matches across segments, `?`
    matches one character that is not a separator. Nothing else is special.
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
class Mapping:
    relay_url: str
    roots: list[str]
    skips: list[tuple[list[str], str]] = field(default_factory=list)
    copies: list[Rule] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Mapping":
        raw = tomllib.loads(Path(path).read_text())
        return cls(
            relay_url=raw["relay_url"],
            roots=raw["roots"],
            skips=[(s["patterns"], s["why"]) for s in raw.get("skip", [])],
            copies=[Rule(c["src"], c["dest"]) for c in raw.get("copy", [])],
        )

    def classify(self, relpath: str) -> tuple[str, str | None]:
        """Return ("skip"|"copy", destination) for one relay-relative path."""
        for patterns, _why in self.skips:
            if any(globmatch(relpath, p) for p in patterns):
                return "skip", None
        for rule in self.copies:
            if globmatch(relpath, rule.src):
                if rule.dest.endswith("/"):
                    return "copy", rule.dest + Path(relpath).name
                return "copy", rule.dest
        return "unmatched", None


def walk_surface(relay_root: Path, mapping: Mapping):
    """Yield every relay-relative file path under the mapped roots, sorted."""
    for root in mapping.roots:
        base = relay_root / root
        if not base.is_dir():
            raise VendorError(f"relay root {root!r} does not exist in {relay_root}")
        for path in sorted(base.rglob("*")):
            if path.is_file() or path.is_symlink():
                yield str(path.relative_to(relay_root))


def plan(relay_root: Path, mapping: Mapping) -> dict[str, str]:
    """Resolve the mapping against a relay checkout.

    Raises VendorError listing every uncovered file, because an upstream
    refactor usually adds several at once and fixing them one error at a
    time is needless work.
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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sync(relay_root: Path, vendor_dir: Path, mapping: Mapping) -> dict[str, str]:
    """Copy the mapped files into vendor_dir, returning dest -> sha256."""
    mapped = plan(relay_root, mapping)
    if vendor_dir.exists():
        shutil.rmtree(vendor_dir)
    hashes = {}
    for src, dest in sorted(mapped.items()):
        target = vendor_dir / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(relay_root / src, target)
        hashes[dest] = sha256_file(target)
    return hashes


def verify(vendor_dir: Path, expected: dict[str, str]) -> list[str]:
    """Return a list of human-readable differences against vendor.lock."""
    problems = []
    present = {
        str(p.relative_to(vendor_dir)) for p in vendor_dir.rglob("*") if p.is_file()
    }
    for dest, want in sorted(expected.items()):
        path = vendor_dir / dest
        if not path.is_file():
            problems.append(f"missing: {dest}")
        elif sha256_file(path) != want:
            problems.append(f"modified: {dest}")
    for extra in sorted(present - set(expected)):
        problems.append(f"unexpected: {extra}")
    return problems
