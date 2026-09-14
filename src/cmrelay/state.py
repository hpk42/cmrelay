"""The facts about a host that no chatmail.ini records.

Files under /opt/cmrelay/state: which units take-over disabled or masked,
what the operator asked for that the ini cannot express, and which web
pages are published.
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


def read_intent() -> dict:
    try:
        data = json.loads(paths.INTENT.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_intent(journal, intent: dict) -> None:
    text = json.dumps(intent, indent=2, sort_keys=True) + "\n"
    journal.write("cmrelay", paths.INTENT, text)


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
