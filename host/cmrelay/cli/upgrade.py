"""`cmrelay upgrade`: install a newer signed release, or with --force any release.

All or nothing: when the new release does not deploy, the old one is deployed again.
"""

import os
import subprocess
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version

from .. import CmrelayError, paths
from ..release import signature, store
from ..release.bundle import ENV_CLOSURE, ENV_STAGE, Bundle
from ..system import fsops


def upgrade_verb(args, bundle, out) -> int:
    candidate = Path(args.file).expanduser().resolve()
    if not candidate.is_file():
        raise CmrelayError(f"no installer at {candidate}")

    changes = fsops.Changes()
    # the release every service goes back to when the new one does not deploy
    old_cli, old_paths = paths.CLI.readlink(), paths.STORE_PATHS.read_text()
    # Where the candidate is copied and unpacked; the deploy lock keeps it private.
    with fsops.lock(), fsops.scratch(paths.STAGING):
        # Verify and execute the same root-owned copy.
        copy = paths.STAGING / "candidate.run"
        sig = signature.detach(candidate, copy)
        copy.chmod(0o700)
        signer = check_signature(bundle, copy, sig, args, out)
        staged = stage(copy, out)
        [closure] = (staged / "lib/store").glob("*-cmrelay-closure")
        new = Bundle(paths.STORE / closure.name, staged)
        have, new_version = bundle.manifest["version"], new.manifest["version"]
        try:
            newer = Version(new_version) > Version(have)
        except InvalidVersion:
            newer = False
        if not (newer or args.force):
            raise CmrelayError(
                f"{new_version} is not newer than the installed {have};"
                " --force installs it anyway"
            )
        announce(out, bundle, new, signer)
        if not out.confirm("Upgrade?", args.yes):
            return 1
        store.install(changes, new, out)
    out.ok(f"{new_version} is installed; deploying it")
    flags = {"--yes": args.yes, "--skip-dns-check": args.skip_dns_check}
    deploy = ["deploy", *(flag for flag, given in flags.items() if given)]
    if run_installed(deploy) == 0:
        return 0
    # all or nothing: the deploy of the old release puts every service back on it
    out.error(f"{new_version} did not deploy; going back to {have}")
    with fsops.lock():
        changes.write("cmrelay", paths.STORE_PATHS, old_paths)
        changes.symlink("cmrelay", paths.CLI, old_cli)
    if run_installed(deploy):
        raise CmrelayError(f"{have} did not deploy either; see above")
    out.error(f"{new_version} is not installed; {have} runs as before")
    return 1


def run_installed(argv) -> int:
    """Run the CLI the store link points at, which takes the deploy lock itself."""
    installed = paths.CLI.resolve()
    if not installed.is_file():
        raise CmrelayError(f"the installed closure has no CLI at {installed}")
    env = dict(os.environ)
    # Bundle.discover() keys off these.
    for name in (ENV_STAGE, ENV_CLOSURE, "PYTHONPATH"):
        env.pop(name, None)
    sys.stdout.flush()
    return subprocess.run([installed, *argv], env=env, check=False).returncode


def check_signature(bundle, candidate: Path, sig, args, out) -> str:
    """The release key's fingerprint, or "none" with --no-verify."""
    if args.no_verify:
        out.warn("installing without verifying the signature (--no-verify)")
        out.print(f"  sha256 {fsops.file_digest(candidate)}")
        return "none"
    key = bundle.public_key
    if signature.verify(bundle, candidate, sig):
        fingerprint = signature.fingerprint(key.read_bytes())
        out.ok(f"signature verified by the release key ({fingerprint})")
        return fingerprint
    if not key:
        reason = "this cmrelay carries no release key to verify with"
    elif not sig:
        reason = "the installer carries no signature"
    else:
        reason = (
            "the release key does not verify this installer: it was altered on the way"
        )
    raise CmrelayError(f"{reason}; --no-verify installs it without checking")


def stage(candidate: Path, out) -> Path:
    """Unpack the candidate and stop. It never runs as an installer."""
    result = subprocess.run(
        [str(candidate), "--stage-only"], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise CmrelayError(
            f"{candidate} could not unpack itself:\n{result.stderr.strip()}"
        )
    staged = Path(result.stdout.strip().splitlines()[-1])
    if not (staged / "lib/store").is_dir():
        raise CmrelayError(f"{candidate} did not unpack a store into {staged}")
    out.detail(f"staged in {staged}")
    return staged


def announce(out, bundle, new, signer) -> None:
    out.print("")
    out.print(f"  installed   {bundle.manifest['version']}")
    out.print(f"  candidate   {new.manifest['version']}")
    out.print(f"  chatmail/relay {bundle.relay_commit[:12]} -> {new.relay_commit[:12]}")
    out.print(f"  signed by   {signer}")
    files = (p for p in new.prefix.rglob("*") if p.is_file() and not p.is_symlink())
    out.print(f"  unpacked    {sum(p.stat().st_size for p in files) >> 20} MiB")
