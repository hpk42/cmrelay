"""Discover and execute the wheel-contained cmrelay binbary."""

# needed on Python 3.9 (RHEL 9)
from __future__ import annotations

import os
import sys
from pathlib import Path

# Written here by scripts/wheel-cmrelay.py; absent in a development build.
BUNDLED = Path(__file__).parent / "_payload" / "cmrelay.run"


class PayloadError(Exception):
    pass


def bundled_payload(bundled: Path = BUNDLED) -> Path | None:
    return bundled if bundled.is_file() else None


def exec_payload(payload: Path, argv: list[str]) -> int:
    """Run the payload with the original arguments. Does not return on success.

    TMPDIR matters: the stub unpacks the closure there, and /tmp is a tmpfs on
    several of the target distributions while the closure is ~250 MiB open.
    """
    if not os.access(payload, os.X_OK):
        try:
            payload.chmod(payload.stat().st_mode | 0o111)
        except OSError as exc:
            raise PayloadError(
                f"{payload} is not executable and cannot be made so: {exc}"
            ) from exc
    env = dict(os.environ)
    env.setdefault("TMPDIR", "/var/tmp")
    argv = [str(payload), *argv]
    os.execve(str(payload), argv, env)


def run_from_wheel(args_list: list[str], out, bundled: Path = BUNDLED) -> int:
    if sys.platform != "linux":
        raise PayloadError(f"cmrelay installs relays on Linux, not {sys.platform}")
    payload = bundled_payload(bundled)
    if payload is None:
        raise PayloadError(
            "this cmrelay carries no payload, so it was built "
            " from a source checkout rather than installed from a release wheel."
            " Build one with `nix build '.?submodules=1#payload'` and run the resulting"
            " .run file directly, or `nix build '.?submodules=1#wheel'` for a"
            " wheel with the payload inside it."
        )

    out.section("Starting the bundled cmrelay payload")
    out.print(f"  handing over to {payload}")
    sys.stdout.flush()
    exec_payload(payload, args_list)
    return 1  # unreachable unless execve fails
