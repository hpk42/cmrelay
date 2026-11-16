"""Move a Nix store from /nix/store to /opt/cmrelay/lib/store, in place.

Paths keep their length: the new prefix is longer, so the hash is cut to 20
characters. A hash without the store prefix is reported and fails the build.

    relocate.py STORE [PATH...]
"""

import os
import re
import sys
from pathlib import Path

OLD = b"/nix/store/"
NEW = b"/opt/cmrelay/lib/store/"
HASH = 32
KEEP = HASH - (len(NEW) - len(OLD))

# Wider than Nix's base32, so that remove-references-to's eeee... moves too.
REFERENCE = re.compile(re.escape(OLD) + rb"([0-9a-z]{%d})" % HASH)
BARE = re.compile(rb"(?<![0-9a-z])[0-9a-z]{%d}(?![0-9a-z])" % HASH)

# nginx records its configure flags, which name build sandbox directories.
BUILD_DIR = b"/build/"


def relocate_bytes(data: bytes) -> bytes:
    return REFERENCE.sub(lambda m: NEW + m.group(1)[:KEEP], data)


def relocate_store(store: Path) -> list:
    """Rewrite *store* in place; return whatever still names an old path."""
    hashes = {p.name[:HASH].encode() for p in store.iterdir()}
    for path in store.rglob("*"):
        if path.is_symlink():
            target = os.fsencode(os.readlink(path))
            if OLD in target:
                path.unlink()
                path.symlink_to(os.fsdecode(relocate_bytes(target)))
        elif path.is_file():
            data = path.read_bytes()
            if OLD in data:
                path.write_bytes(relocate_bytes(data))
    for entry in store.iterdir():
        entry.rename(store / (entry.name[:KEEP] + entry.name[HASH:]))
    return list(leftovers(store, hashes))


def leftovers(store: Path, hashes: set):
    for path in store.rglob("*"):
        if path.is_symlink():
            data = os.fsencode(os.readlink(path))
        else:
            data = path.read_bytes() if path.is_file() else b""
        blob = os.fsencode(path.relative_to(store)) + b"\0" + data
        found = {
            m.group()
            for m in BARE.finditer(blob)
            if m.group() in hashes
            and blob[m.start() - len(BUILD_DIR) : m.start()] != BUILD_DIR
        }
        for name in sorted(found):
            yield f"{path.relative_to(store)} names {name.decode()}"


def main(argv) -> int:
    store, *wanted = argv
    problems = relocate_store(Path(store))
    for problem in problems:
        print(f"relocate: {problem}", file=sys.stderr)
    if problems:
        return 1
    for path in wanted:
        print(relocate_bytes(path.encode()).decode())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
