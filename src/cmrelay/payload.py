"""Where a pip-installed cmrelay finds its payload: inside itself.

A wheel is a convenient way to *reach* cmrelay and a useless way to *be* it.
The relay is a Nix closure, and the installer has to run on the interpreter
from inside it rather than whatever pip installed the CLI into. So a
pip-installed cmrelay does one thing: it execs the payload beside it, and
everything proceeds exactly as if the operator had copied the .run file
across themselves, which is the only path that is actually tested.

    pip install cmrelay
    cmrelay install --mail-domain relay.example.org

Every published wheel is per-architecture and carries its own payload, so the
second line reaches nothing. There is deliberately no wheel that downloads
one, and therefore no code here that could: cmrelay never makes a network
request, on any path, and an installer that never fetches has nothing to
intercept. `pip` is the one step that talks to the internet, and after it a
firewalled or air-gapped host is fully served.

A wheel built straight from a source checkout has no payload, because only
scripts/wheel-cmrelay.py puts one in. That wheel says so and stops.
"""

import os
import shutil
import sys
from pathlib import Path

# Written here by scripts/wheel-cmrelay.py. Its absence means this is a
# development build rather than a release.
BUNDLED = Path(__file__).parent / "_payload" / "cmrelay.run"


class PayloadError(Exception):
    pass


def bundled_payload() -> Path | None:
    """The payload inside this wheel, if this wheel has one."""
    return BUNDLED if BUNDLED.is_file() else None


def exec_payload(payload: Path, argv: list[str]) -> int:
    """Run the payload with the original arguments. Does not return on success.

    TMPDIR matters: the stub unpacks the closure there, and /tmp is a tmpfs on
    several of the target distributions while the closure is ~250 MiB open.
    """
    if not os.access(payload, os.X_OK):
        # The wheel records mode 755, but an installer that flattens modes --
        # or an unzip into a noexec-adjacent tree -- would leave it unrunnable,
        # and `Permission denied` from execve does not say which of the two
        # went wrong.
        try:
            payload.chmod(payload.stat().st_mode | 0o111)
        except OSError as exc:
            raise PayloadError(
                f"{payload} is not executable and cannot be made so: {exc}"
            ) from exc
    env = dict(os.environ)
    env.setdefault("TMPDIR", "/var/tmp")
    argv = [str(payload), "--quiet", "--", *argv]
    os.execve(str(payload), argv, env)


def run_from_wheel(args_list: list[str], out) -> int:
    """Handle a verb that needs the closure, from a CLI that has no closure."""
    if sys.platform != "linux":
        raise PayloadError(f"cmrelay installs relays on Linux, not {sys.platform}")
    if not shutil.which("tar"):
        raise PayloadError("cmrelay needs tar on the host to unpack its payload")

    payload = bundled_payload()
    if payload is None:
        raise PayloadError(
            "this cmrelay carries no payload, so it was built from a source"
            " checkout rather than installed from a release wheel. Build one"
            " with `nix build '.?submodules=1#payload'` and run the resulting"
            " .run file directly, or `nix build '.?submodules=1#wheel'` for a"
            " wheel with the payload inside it."
        )

    out.section("Starting the bundled cmrelay payload")
    out.print(f"  handing over to {payload}")
    sys.stdout.flush()
    exec_payload(payload, args_list)
    return 1  # unreachable unless execve fails
