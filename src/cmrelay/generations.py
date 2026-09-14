"""One directory per installed closure, and a symlink saying which is live.

Store paths are content-addressed, so installing a new closure adds paths and
removes none: the previous release stays complete and runnable at no copying
cost, and rollback is one rename.
"""

import re
from pathlib import Path

from . import paths

_NUMBER = re.compile(r"^\d+$")


def existing() -> list:
    if not paths.GENERATIONS.is_dir():
        return []
    numbers = [
        int(p.name)
        for p in paths.GENERATIONS.iterdir()
        if _NUMBER.match(p.name) and p.is_symlink()
    ]
    return sorted(numbers)


def current() -> int | None:
    link = paths.CURRENT
    if not link.is_symlink():
        return None
    name = link.readlink().name
    return int(name) if _NUMBER.match(name) else None


def next_number() -> int:
    numbers = existing()
    return (max(numbers) + 1) if numbers else 1


def create(journal, number: int, closure: Path, store_names) -> None:
    """Link the generation to its closure and record the store paths it needs."""
    # relative, so the whole tree can be moved
    relative = Path("..") / "nix" / "store" / Path(closure).name
    link = paths.GENERATIONS / str(number)
    journal.symlink("cmrelay", link, relative, detail=str(closure))
    listing = "\n".join(sorted(store_names)) + "\n"
    journal.write("cmrelay", paths.GENERATIONS / f"{number}.paths", listing)


def flip(journal, number: int) -> None:
    journal.symlink("cmrelay", paths.CURRENT, Path(str(number)))
    journal.symlink(
        "cmrelay", paths.CLI, Path("..") / "generations" / "current" / "bin" / "cmrelay"
    )


def live_store_paths(numbers) -> set:
    names = set()
    for number in numbers:
        record = paths.GENERATIONS / f"{number}.paths"
        if record.is_file():
            names.update(record.read_text().split())
    return names


def prune(journal, out, keep: int = 2) -> list:
    """Keep the current generation and one previous; delete the rest."""
    numbers = existing()
    live = current()
    if live is None or len(numbers) <= keep:
        return []
    keeping = sorted(n for n in numbers if n <= live)[-keep:]
    keeping += [n for n in numbers if n > live]
    doomed = [n for n in numbers if n not in keeping]
    protected = live_store_paths(keeping)
    for number in doomed:
        for name in sorted(live_store_paths([number]) - protected):
            path = paths.STORE / name
            journal.remove_directory("generations", path)
            journal.remove_file("generations", path)
        journal.remove_file("generations", paths.GENERATIONS / str(number))
        journal.remove_file("generations", paths.GENERATIONS / f"{number}.paths")
        out.detail(f"removed generation {number}")
    return doomed
