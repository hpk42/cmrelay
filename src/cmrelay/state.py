"""The facts about a host that no chatmail.ini records.

Files under /opt/cmrelay/state: what cmrelay wrote, what the operator asked
for that the ini cannot express, which web pages are published, which
migrations ran, and whether another deploy is running.
"""

import json
from pathlib import Path

from . import paths


def read_lines(path: Path) -> list:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def record_units(journal, path: Path, names) -> None:
    """Add to what a state file already lists, never replacing it."""
    merged = sorted(set(read_lines(path)) | set(names))
    journal.write("cmrelay", path, "\n".join(merged) + "\n")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(journal, component: str, path: Path, data) -> None:
    journal.write(component, path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_written() -> dict:
    """path -> sha256, for every file cmrelay wrote outside its own tree."""
    data = read_json(paths.WRITTEN, {})
    return data if isinstance(data, dict) else {}


def save_written(journal, previous: dict) -> None:
    """Fold this run's writes into the record, keeping what it did not touch."""
    merged = dict(previous)
    merged.update(journal.wrote)
    for path in list(merged):
        if not Path(path).exists():
            del merged[path]
    write_json(journal, "cmrelay", paths.WRITTEN, merged)


def read_intent() -> dict:
    data = read_json(paths.INTENT, {})
    return data if isinstance(data, dict) else {}


def save_intent(journal, intent: dict) -> None:
    write_json(journal, "cmrelay", paths.INTENT, intent)


def resolve_mail_intent(previous: dict, enable=None) -> bool:
    """Whether this host is meant to be running postfix and dovecot.

    `--disable-mail` is a standing decision, not a flag on one run.
    """
    if enable is None:
        return not previous.get("disable_mail", False)
    return enable


def read_www_files() -> list:
    return read_lines(paths.WWW_FILES)


def save_www_files(journal, names) -> None:
    journal.write("website", paths.WWW_FILES, "\n".join(sorted(names)) + "\n")


def read_migrations() -> set:
    return set(read_lines(paths.MIGRATIONS))


def save_migrations(journal, names) -> None:
    journal.write("cmrelay", paths.MIGRATIONS, "\n".join(sorted(names)) + "\n")
