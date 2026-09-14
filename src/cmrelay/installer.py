"""What the `.run` file does, and nothing more.

Unpacking the archive happens once per host per release; deploying happens
whenever configuration changes. So the installer publishes the store, links
the CLI and stops.
"""

import os
import subprocess
import sys
from pathlib import Path

from . import fsops, generations, paths, store
from .bundle import ENV_CLOSURE, ENV_STAGE, READY_MARKER, Bundle
from .errors import CmrelayError
from .version import __version__


class InstallerError(CmrelayError):
    pass


def installer_verb(args, out) -> int:
    if os.geteuid() != 0:
        raise InstallerError("cmrelay must run as root")
    bundle = Bundle.discover()
    if bundle.prefix is None:
        raise InstallerError(
            "this cmrelay is already installed; `cmrelay deploy` is what runs"
            " from here, and `cmrelay upgrade` replaces the release"
        )

    if args.stage_only:
        print(bundle.prefix)
        return 0

    if not args.yes and not confirm(bundle, out):
        out.print("aborted; nothing was modified")
        return 1

    publish(bundle, fsops.Journal(), out)
    hint = "" if paths.INI.is_file() else " relay.example.org"
    out.print("\nInstalled; nothing is configured or running yet. Next:")
    out.print(f"\n    cmrelay deploy{hint}")
    return 0


def staged_size(prefix: Path) -> str:
    result = subprocess.run(
        ["du", "-sh", str(prefix)], capture_output=True, text=True, check=False
    )
    return result.stdout.split("\t", 1)[0].strip() or "unknown"


def confirm(bundle, out) -> bool:
    machine = os.uname().machine
    out.print(
        f"cmrelay-installer {__version__}"
        f" (relay {bundle.relay_commit[:10]}, {machine})\n"
    )
    out.print("This will write:")
    out.print(
        f"  {str(paths.ROOT) + '/':<36} the relay stack, {staged_size(bundle.prefix)}"
    )
    out.print(f"  {'/nix':<36} a symlink to {paths.NIX}")
    out.print(f"  {str(paths.LOCAL_BIN / 'cmrelay'):<36} the CLI")
    out.print(f"  {str(paths.PROFILE_SCRIPT):<36} puts {paths.BIN} on PATH")
    out.print("\nNothing else is touched until `cmrelay deploy` runs.")
    return out.confirm("Install?")


def publish(bundle, journal, out) -> None:
    """Move the closure into place and give the CLI a stable name."""
    stage_store = bundle.prefix / "nix/store"
    names = sorted(p.name for p in stage_store.iterdir())
    journal.ensure_directory("cmrelay", paths.ROOT)
    # Before the first rename, so an interrupted run leaves no marker on a
    # staging tree the stub would offer to the next run as whole.
    journal.remove_file("cmrelay", bundle.prefix / READY_MARKER)

    moved = store.install(stage_store)
    store.link_nix()
    note = store.label_for_selinux()
    if note:
        out.print(f"  {note}")
    out.print(f"  {moved} store paths under {paths.STORE}, /nix -> {paths.NIX}")

    number = generations.next_number()
    generations.create(journal, number, Path(bundle.closure), names)
    generations.flip(journal, number)
    journal.symlink("cmrelay", paths.LOCAL_BIN / "cmrelay", paths.CLI)
    write_profile_script(journal)
    out.print(f"  {paths.CLI} -> generation {number}")


def write_profile_script(journal) -> bool:
    if not paths.PROFILE_D.is_dir():
        return False
    journal.write(
        "tools",
        paths.PROFILE_SCRIPT,
        "# written by cmrelay; removed by `cmrelay uninstall`\n"
        'case ":$PATH:" in\n'
        f"    *:{paths.BIN}:*) ;;\n"
        f'    *) PATH="$PATH:{paths.BIN}" ;;\n'
        "esac\n"
        "export PATH\n",
    )
    return True


def exec_installed(argv) -> None:
    """Hand over to the CLI now living in the store. Does not return."""
    installed = paths.CLI.resolve()
    if not installed.is_file():
        raise InstallerError(f"the installed closure has no CLI at {installed}")
    env = dict(os.environ)
    # Bundle.discover() keys off these; without them it locates the closure
    # from its own __file__, which is now the installed copy.
    for name in (ENV_STAGE, ENV_CLOSURE, "PYTHONPATH"):
        env.pop(name, None)
    sys.stdout.flush()
    os.execve(str(installed), [str(installed), *argv], env)
