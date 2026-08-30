"""What an upgrade has to do that `deploy` does not already do.

`deploy` regenerates every config file and every unit from scratch, so a
changed template, a changed unit, a new service and a dropped service all
need nothing here. What needs a migration is narrow:

- a path that moved, whose old location must be removed or copied from
- a unit that was renamed, whose old name must be stopped and its file deleted
- a state file whose format changed
- a user or group whose membership changed

Migrations do not decide restarts. `deploy` already restarts exactly the
services whose inputs changed, and it runs every time rather than once per
release.

Every migration is written to be safe to re-run: a partial failure then
re-runs cleanly instead of needing its own recovery reasoning.
"""

from dataclasses import dataclass

from .version import parse_version


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    describe: str
    run: object


MIGRATIONS: list = []


def pending(installed: str, candidate: str, done=(), migrations=None) -> list:
    """Every migration newer than what is installed and not already applied.

    An unparseable version on either side selects nothing: guessing an order
    between two names that are not versions would run migrations at random.
    """
    have, want = parse_version(installed), parse_version(candidate)
    if have is None or want is None:
        return []
    chosen = []
    for migration in MIGRATIONS if migrations is None else migrations:
        at = parse_version(migration.version)
        if at is None or migration.name in done:
            continue
        if have < at <= want:
            chosen.append(migration)
    return sorted(chosen, key=lambda m: (parse_version(m.version), m.name))


def apply(migrations, journal, out) -> list:
    applied = []
    for migration in migrations:
        out.detail(f"{migration.name}: {migration.describe}")
        migration.run(journal, out)
        applied.append(migration.name)
    return applied
