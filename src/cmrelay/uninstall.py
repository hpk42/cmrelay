"""`cmrelay uninstall`: hand the host back to `cmdeploy run`.

Nothing is started here, deliberately: starting the distribution's stack
while cmrelay's is being torn down would race it.
"""

from pathlib import Path

from . import activation, fsops, host, paths, state, store, systemd


def uninstall_verb(args, out) -> int:
    recorded = host.config_domain(paths.INI)
    if args.domain and recorded and args.domain != recorded:
        raise host.DomainRefused(
            f"this host serves '{recorded}', not '{args.domain}'."
            f"\n  Name it to confirm:  cmrelay uninstall {recorded}"
        )
    if not args.domain and not args.yes:
        hint = f"  cmrelay uninstall {recorded}" if recorded else ""
        raise host.DomainRefused(
            f"cmrelay uninstall needs the domain of this relay to confirm.\n{hint}"
        )

    journal = fsops.Journal()
    out.section("Stopping cmrelay units")
    stop_our_units(out, journal)

    out.section("Unmasking what take-over masked")
    masked = state.read_lines(paths.DISABLED_UNITS)
    for name in masked:
        systemd.unmask(name, reload=False)
        out.print(f"  unmasked {name}")
    if not masked:
        out.print("  nothing to unmask")

    # Enabled, not started: starting here would race the stack being torn
    # down, and relay's unbound deployer runs `systemctl reset-failed`, which
    # answers "not loaded" for a disabled unit.
    out.section("Re-enabling what take-over disabled")
    shadowed = state.read_lines(paths.SHADOWED_UNITS_STATE)
    for name in shadowed:
        systemd.enable(name, reload=False)
        out.print(f"  enabled {name}")
    if not shadowed:
        out.print("  nothing to re-enable")
    out.print("\nStarting them is `cmdeploy run`'s job, not this one's.")

    if systemd.tmpfiles_create():
        out.print("  recreated the /run directories tmpfiles.d declares")

    remove_linked_tools(out, journal)
    diff_configs(out)
    report_own_pages(out)

    out.section("Removing the tree")
    if store.unlink_nix():
        out.print("  removed /nix")
    if journal.remove_directory("cmrelay", paths.ROOT):
        out.print(f"  removed {paths.ROOT}")
    # so the next classification does not take a bare host for a cmdeploy one
    journal.remove_file("cmrelay", paths.VERSION_MARKER)

    systemd.daemon_reload()
    out.section("Done")
    out.print("Roll back to the Debian stack with: cmdeploy run")
    return 0


def report_own_pages(out):
    """The one thing here with no copy anywhere else."""
    if not paths.WWW_SRC.is_dir():
        return
    pages = sorted(p.name for p in paths.WWW_SRC.iterdir() if p.is_file())
    if not pages:
        return
    out.warn(
        f"{paths.WWW_SRC} holds your own web page source ({', '.join(pages)})"
        " and is about to be removed with the rest of the tree."
    )


def stop_our_units(out, journal):
    if not paths.UNITS.is_dir():
        return
    for unit in sorted(paths.UNITS.glob("*")):
        name = unit.name
        systemd.systemctl("stop", name, check=False)
        systemd.systemctl("disable", "--no-reload", name, check=False)
        journal.remove_file("cmrelay", paths.unit_file(name))
        out.print(f"  stopped and removed {name}")
    for name in activation.AUTHORED_UNITS:
        journal.remove_directory("cmrelay", paths.SYSTEMD_SYSTEM / f"{name}.service.d")


def remove_linked_tools(out, journal):
    """Take back /usr/local/bin, so nothing points into a deleted store."""
    if not paths.LINKED_TOOLS.is_file():
        return
    out.section("Removing tool links")
    for name in paths.LINKED_TOOLS.read_text().split():
        link = paths.LOCAL_BIN / name
        if not is_ours(link):
            continue
        journal.remove_file("tools", link)
        out.print(f"  removed {link}")
    remove_profile_script(out, journal)


def remove_profile_script(out, journal):
    script = paths.PROFILE_SCRIPT
    if not script.is_file():
        return
    if "written by cmrelay" not in script.read_text():
        out.print(f"  leaving {script} alone: cmrelay did not write it")
        return
    journal.remove_file("tools", script)
    out.print(f"  removed {script}")


def is_ours(link: Path) -> bool:
    """Removing by name alone would delete whatever had since taken it."""
    if link.is_symlink():
        return str(link.readlink()).startswith(str(paths.ROOT))
    if link.is_file():
        try:
            return "written by cmrelay" in link.read_text()
        except (OSError, UnicodeDecodeError):
            return False
    return False


def diff_configs(out):
    mine, theirs = paths.INI, paths.CMDEPLOY_INI
    if not (mine.is_file() and theirs.is_file()):
        return
    if mine.read_bytes() == theirs.read_bytes():
        return
    out.warn(
        f"{mine} differs from {theirs}."
        " `cmdeploy run` regenerates its own ini from its checkout, so"
        " configuration edits made under cmrelay will not survive a rollback."
    )
