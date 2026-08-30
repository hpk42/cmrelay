"""One directory per installed closure, and a symlink saying which is live.

Store paths are content-addressed, so installing a new closure adds paths and
removes none: the previous release stays complete and runnable at no copying
cost, and rollback is one rename. What is not content-addressed is small --
chatmail.ini, www/, state/ and the generated units -- and is copied beside
each generation before the flip.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import paths

# Copied into <n>.state before a flip. Kilobytes, and the only things a
# rollback cannot reconstruct from the closure.
STATE_MEMBERS = ("chatmail.ini", "www", "state")

_NUMBER = re.compile(r"^\d+$")


@dataclass(frozen=True)
class Generation:
    number: int
    closure: Path

    @property
    def link(self) -> Path:
        return paths.GENERATIONS / str(self.number)

    @property
    def store_paths(self) -> Path:
        return paths.GENERATIONS / f"{self.number}.paths"


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


def relative_closure(closure: Path) -> Path:
    """The closure as `../nix/store/<name>`, so the tree can be moved."""
    return Path("..") / "nix" / "store" / Path(closure).name


def create(journal, number: int, closure: Path, store_names=()) -> Generation:
    generation = Generation(number, Path(closure))
    journal.symlink(
        "cmrelay", generation.link, relative_closure(closure), detail=str(closure)
    )
    if store_names:
        journal.write(
            "cmrelay", generation.store_paths, "\n".join(sorted(store_names)) + "\n"
        )
    return generation


def _copy_members(journal, source_root, target_root):
    for name in STATE_MEMBERS:
        member = source_root / name
        if member.is_file():
            journal.copy("cmrelay", member, target_root / name)
        elif member.is_dir():
            for path in sorted(member.rglob("*")):
                if path.is_file():
                    journal.copy(
                        "cmrelay", path, target_root / name / path.relative_to(member)
                    )


def snapshot_state(journal, number: int) -> None:
    """Copy the small, non-content-addressed state beside a generation."""
    target = paths.GENERATIONS / f"{number}.state"
    journal.ensure_directory("cmrelay", target)
    _copy_members(journal, paths.ROOT, target)


def restore_state(journal, number: int) -> bool:
    source = paths.GENERATIONS / f"{number}.state"
    if not source.is_dir():
        return False
    _copy_members(journal, source, paths.ROOT)
    return True


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
    if not doomed:
        return []

    protected = live_store_paths(keeping)
    for number in doomed:
        for name in sorted(live_store_paths([number]) - protected):
            path = paths.STORE / name
            journal.remove_directory("generations", path)
            journal.remove_file("generations", path)
        journal.remove_file("generations", paths.GENERATIONS / str(number))
        journal.remove_file("generations", paths.GENERATIONS / f"{number}.paths")
        journal.remove_directory("generations", paths.GENERATIONS / f"{number}.state")
        out.detail(f"removed generation {number}")
    return doomed
