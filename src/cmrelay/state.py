"""The facts about a host that no chatmail.ini records.

Files under /opt/cmrelay/state: what cmrelay wrote, what the operator asked
for that the ini cannot express, which web pages are published, which
migrations ran, and whether another deploy is running.
"""

import json
from pathlib import Path

from . import paths


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
    if not paths.WWW_FILES.is_file():
        return []
    return [
        line.strip() for line in paths.WWW_FILES.read_text().split() if line.strip()
    ]


def save_www_files(journal, names) -> None:
    journal.write("website", paths.WWW_FILES, "\n".join(sorted(names)) + "\n")


def read_migrations() -> set:
    if not paths.MIGRATIONS.is_file():
        return set()
    return {
        line.strip() for line in paths.MIGRATIONS.read_text().split() if line.strip()
    }


def save_migrations(journal, names) -> None:
    journal.write("cmrelay", paths.MIGRATIONS, "\n".join(sorted(names)) + "\n")
