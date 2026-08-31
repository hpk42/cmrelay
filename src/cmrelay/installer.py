"""What the `.run` file does, and nothing more.

Unpacking a 50 MiB archive happens once per host per release; deploying
happens whenever configuration changes. So the installer publishes the store,
links the CLI and stops. It takes no arguments at all beyond -y: offering a
domain would make one command both install and configure, and an operator
who has seen that shortcut has no way to tell which half failed.
"""

import os
import subprocess
import sys
from pathlib import Path

from . import fsops, generations, paths, store
from .bundle import ENV_CLOSURE, ENV_INSTALLER, ENV_STAGE, READY_MARKER, Bundle
from .version import __version__

# Only to phrase the refusal below: a domain wants `cmrelay deploy DOMAIN`
# and a verb wants `cmrelay VERB`. test_cli pins it against the dispatch table.
VERBS = ("deploy", "dns", "install", "status", "uninstall", "upgrade", "version")


class InstallerError(Exception):
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

    if store_populated():
        raise InstallerError(already_installed())
    refuse_arguments(args)

    if not args.yes and not confirm(bundle, out):
        out.print("aborted; nothing was modified")
        return 1

    journal = fsops.Journal()
    publish(bundle, journal, out)
    hint = "" if paths.INI.is_file() else " relay.example.org"
    out.print("\nInstalled; nothing is configured or running yet. Next:")
    out.print(f"\n    cmrelay deploy{hint}")
    return 0


def store_populated(target: Path = paths.STORE) -> bool:
    return target.is_dir() and any(target.iterdir())


def already_installed() -> str:
    """The store is here. Whether that is finished or half-done differs."""
    if not paths.INI.is_file():
        return (
            f"{paths.STORE} already holds a closure, but this host has no"
            f" {paths.INI} yet, so an earlier run did not finish."
            "\n  Carry on from where it stopped:"
            "\n\n      cmrelay deploy [DOMAIN]"
        )
    # Named when this came from a .run file; from `cmrelay install` there is
    # no file to name, and a bare `upgrade` finds the newest release itself.
    installer = os.environ.get(ENV_INSTALLER, "")
    return (
        f"{paths.STORE} already holds an installed relay."
        "\n  To replace the release running here:"
        f"\n\n      {f'cmrelay upgrade {installer}'.rstrip()}"
    )


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


def refuse_arguments(args) -> None:
    """Anything but -y is somebody expecting this to configure the relay too."""
    rest = [a for a in (args.rest or []) if a != "--"]
    if not rest:
        return None
    installer = os.environ.get(ENV_INSTALLER, "<installer>")
    wanted = " ".join(rest if rest[0] in VERBS else ["deploy", *rest])
    raise InstallerError(
        f"the installer installs and takes no arguments, so {rest[0]!r} is"
        " nothing it can do.\n  Install first, then run the command:"
        f"\n\n      {installer}"
        f"\n      cmrelay {wanted}"
    )


def exec_installed(argv) -> None:
    """Hand over to the CLI now living in the store. Does not return."""
    installed = paths.CLI.resolve()
    if not installed.is_file():
        raise InstallerError(f"the installed closure has no CLI at {installed}")
    env = dict(os.environ)
    # Bundle.discover() keys off these; without them it locates the closure
    # from its own __file__, which is now the installed copy.
    env.pop(ENV_STAGE, None)
    env.pop(ENV_CLOSURE, None)
    env.pop(ENV_INSTALLER, None)
    env.pop("PYTHONPATH", None)
    sys.stdout.flush()
    os.execve(str(installed), [str(installed), *argv], env)
