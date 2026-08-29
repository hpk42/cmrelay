"""Where a pip-installed cmrelay finds its payload: inside itself.

The installer has to run on the interpreter from inside the closure rather
than whatever pip installed the CLI into, so a pip-installed cmrelay execs
the payload beside it and proceeds as if the operator had copied the .run
file across themselves. Every published wheel is per-architecture and carries
its own payload, so nothing here ever makes a network request.
"""

# Deferred so `X | None` parses on the 3.9 that RHEL 9 ships: a wheel-installed
# cmrelay parses this far before exec'ing a payload carrying its own 3.14.
from __future__ import annotations

import os
import sys
from pathlib import Path

# Written here by scripts/wheel-cmrelay.py; absent in a development build.
BUNDLED = Path(__file__).parent / "_payload" / "cmrelay.run"


class PayloadError(Exception):
    pass


def bundled_payload() -> Path | None:
    return BUNDLED if BUNDLED.is_file() else None


def exec_payload(payload: Path, argv: list[str]) -> int:
    """Run the payload with the original arguments. Does not return on success.

    TMPDIR matters: the stub unpacks the closure there, and /tmp is a tmpfs on
    several of the target distributions while the closure is ~250 MiB open.
    """
    if not os.access(payload, os.X_OK):
        # execve's `Permission denied` would not say the mode was flattened.
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
    if sys.platform != "linux":
        raise PayloadError(f"cmrelay installs relays on Linux, not {sys.platform}")
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
