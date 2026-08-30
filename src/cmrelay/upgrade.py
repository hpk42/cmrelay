"""Install a newer signed release, with a rollback that works.

The sequence is arranged so that everything before the store is published
modifies nothing a failure would leave broken, publishing is additive, and the
flip is atomic. Only the final deploy can leave a mixed state, and that is
exactly the state `deploy`'s own classification knows how to finish.

A failure after the flip reports and stops rather than rolling back by itself:
the previous generation is intact on disk either way, and an automatic
rollback on a partial failure can turn one problem into two.
"""

import json
import subprocess
import sys
from pathlib import Path

from . import (
    discovery,
    fsops,
    generations,
    migrations,
    paths,
    preflight,
    signing,
    state,
    store,
)
from .bundle import READY_MARKER, Bundle
from .deploy import Deployment
from .installer import exec_installed
from .version import __version__, parse_version

DOWNLOADS = paths.STATE / "downloads"


class UpgradeError(Exception):
    pass


def upgrade_verb(args, bundle, out) -> int:
    if args.finish:
        return finish(args, out)
    if args.rollback:
        return rollback(args, out)

    candidate, signature = obtain(args, bundle, out)
    verdict = check_signature(bundle, candidate, signature, args, out)
    if verdict is None:
        return 1

    staged = stage(candidate, out)
    manifest = read_manifest(staged)
    new_version = manifest.get("version") or ""
    if not newer(new_version, args.force):
        raise UpgradeError(
            f"{new_version or 'that candidate'} is not newer than the"
            f" installed {__version__}; --force installs it anyway"
        )

    steps = migrations.pending(__version__, new_version, state.read_migrations())
    announce(out, new_version, staged, steps, verdict)
    if args.check:
        return 0
    if not args.yes and not out.confirm("Upgrade?"):
        out.print("aborted; nothing was modified")
        return 1

    journal = fsops.Journal(written=state.read_written())
    number = publish(staged, journal, out)
    record_signature(journal, verdict, new_version)
    exec_installed(
        ["upgrade", "--finish", str(number), "--previous-version", __version__]
    )
    return 1


# -- obtaining a candidate -------------------------------------------------


def obtain(args, bundle, out) -> tuple:
    if args.file:
        candidate = Path(args.file).expanduser().resolve()
        if not candidate.is_file():
            raise UpgradeError(f"no installer at {candidate}")
        signature = Path(f"{candidate}.sig")
        return candidate, signature.read_text() if signature.is_file() else ""
    return download(args, bundle, out)


def download(args, bundle, out) -> tuple:
    ca_path = Deployment.ca_path()
    if not args.auto and not args.yes:
        out.print(f"cmrelay upgrade will ask {discovery.GITHUB} and {discovery.PYPI}.")
        if not out.confirm("Check for a newer release?"):
            out.print("aborted; nothing was modified")
            raise UpgradeError("no candidate")

    results, failures = discovery.survey(ca_path, args.source)
    out.print("")
    discovery.report(out, __version__, results, failures)
    if not results:
        raise UpgradeError(
            "neither source answered. Download an installer by hand and run"
            f"\n\n      cmrelay upgrade <file>\n\n  {discovery.GITHUB}"
            f"\n  {discovery.PYPI}"
        )

    chosen = discovery.best(results.values())
    out.print(f"\n  Newest is {chosen.version}, from {chosen.source}.")
    if not args.yes and not out.confirm("  Download and verify?"):
        raise UpgradeError("aborted; nothing was modified")

    journal = fsops.Journal()
    run, signature = discovery.download(chosen, ca_path, DOWNLOADS, journal)
    out.detail(f"sha256 {fsops.file_digest(run)}")
    return run, signature.read_text()


def check_signature(bundle, candidate: Path, signature: str, args, out):
    if not signature:
        if args.allow_unsigned:
            unsigned_warning(out, candidate)
            return signing.Verdict(True, key="none")
        raise UpgradeError(
            f"there is no {candidate.name}.sig beside {candidate}, so nothing"
            " says this file is the release it claims to be. A locally built"
            " payload needs --allow-unsigned."
        )
    verdict = signing.verify(bundle, candidate, signature)
    if verdict.ok:
        out.ok(f"signature verified by {verdict.key} ({verdict.fingerprint})")
        return verdict
    if args.allow_unsigned:
        unsigned_warning(out, candidate, verdict.reason)
        return signing.Verdict(True, key="none")
    raise UpgradeError(verdict.reason)


def unsigned_warning(out, candidate: Path, reason: str = "") -> None:
    message = f"installing {candidate} without a verified signature."
    if reason:
        message += f"\n  {reason}"
    out.warn(message)
    out.print(
        "  Nothing here says these bytes are a cmrelay release rather than"
        "\n  something an intermediary substituted for one. --allow-unsigned"
        "\n  is for a payload you built yourself and can account for."
        f"\n  sha256 {fsops.file_digest(candidate)}"
    )


# -- staging and publishing ------------------------------------------------


def stage(candidate: Path, out) -> Path:
    """Unpack the candidate and stop. It never runs as an installer."""
    space = preflight.check_free_space(preflight.CLOSURE_MIB)
    if not space.ok:
        raise UpgradeError(str(space.findings[0]))
    result = subprocess.run(
        [str(candidate), "--stage-only"], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise UpgradeError(
            f"{candidate} could not unpack itself:\n{result.stderr.strip()}"
        )
    staged = Path(result.stdout.strip().splitlines()[-1])
    if not (staged / "nix/store").is_dir():
        raise UpgradeError(f"{candidate} did not unpack a store into {staged}")
    out.detail(f"staged in {staged}")
    return staged


def staged_closure(staged: Path) -> Path:
    candidates = sorted((staged / "nix/store").glob("*-cmrelay-closure"))
    if len(candidates) != 1:
        raise UpgradeError(
            f"expected one cmrelay closure under {staged}, found {len(candidates)}"
        )
    return candidates[0]


def read_manifest(staged: Path) -> dict:
    try:
        return json.loads((staged_closure(staged) / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        raise UpgradeError(
            f"the staged closure has no readable manifest: {exc}"
        ) from exc


def newer(candidate: str, force: bool) -> bool:
    if force:
        return True
    have, want = parse_version(__version__), parse_version(candidate)
    if have is None or want is None:
        return False
    return want > have


def announce(out, new_version, staged, steps, verdict) -> None:
    out.print("")
    out.print(f"  installed   {__version__}")
    out.print(f"  candidate   {new_version}")
    out.print(
        f"  relay       {Bundle.discover().relay_commit[:12]} -> {relay_of(staged)}"
    )
    if verdict.key and verdict.key != "none":
        out.print(f"  signed by   {verdict.key} ({verdict.fingerprint})")
    if steps:
        out.print("  migrations")
        for step in steps:
            out.print(f"    {step.name:<28}{step.describe}")
    else:
        out.print("  migrations  none")
    out.print(f"  disk        {directory_size(staged) // (1024 * 1024)} MiB to publish")


def relay_of(staged: Path) -> str:
    try:
        lock = json.loads((staged_closure(staged) / "vendor.lock").read_text())
        return lock["relay_commit"][:12]
    except (OSError, ValueError, KeyError):
        return "unknown"


def directory_size(path: Path) -> int:
    total = 0
    for entry in Path(path).rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            total += entry.stat().st_size
    return total


def publish(staged: Path, journal, out) -> int:
    """Additive: new store paths land, the previous generation stays whole."""
    closure = staged_closure(staged)
    names = sorted(p.name for p in (staged / "nix/store").iterdir())
    # The store is about to move out from under it, so the tree must stop
    # looking whole to the next installer run.
    journal.remove_file("cmrelay", staged / READY_MARKER)
    number = generations.next_number()
    generations.snapshot_state(journal, generations.current() or 0)
    moved = store.install(staged / "nix/store")
    store.label_for_selinux()
    out.print(f"  published {moved} new store paths")
    generations.create(journal, number, closure, names)
    return number


def record_signature(journal, verdict, version: str) -> None:
    state.write_json(
        journal,
        "cmrelay",
        paths.SIGNATURE,
        {"version": version, "key": verdict.key, "fingerprint": verdict.fingerprint},
    )


# -- the second half, running from the new closure -------------------------


def finish(args, out) -> int:
    """Migrations, the flip, and the deploy that does the rest."""
    number = int(args.finish)
    journal = fsops.Journal(written=state.read_written())
    previous = args.previous_version or ""

    steps = migrations.pending(previous, __version__, state.read_migrations())
    if steps:
        with out.step("Migrations"):
            applied = migrations.apply(steps, journal, out)
            state.save_migrations(journal, set(state.read_migrations()) | set(applied))

    generations.flip(journal, number)
    generations.prune(journal, out)
    state.save_written(journal, journal.written)
    out.ok(f"generation {number} is live; deploying {__version__}")
    sys.stdout.flush()
    exec_installed(["deploy"])
    return 1


def rollback(args, out) -> int:
    numbers = generations.existing()
    live = generations.current()
    previous = [n for n in numbers if n != live]
    if not previous:
        raise UpgradeError("there is no previous generation to roll back to")
    target = max(previous)

    out.print(f"rolling back from generation {live} to {target}")
    if not args.yes and not out.confirm("Continue?"):
        out.print("aborted; nothing was modified")
        return 1

    journal = fsops.Journal(written=state.read_written())
    if not generations.restore_state(journal, target):
        out.warn(f"generation {target} has no saved state; keeping the current one")
    generations.flip(journal, target)
    state.save_written(journal, journal.written)
    exec_installed(["deploy"])
    return 1
