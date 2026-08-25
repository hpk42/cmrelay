"""The `pip install cmrelay` path: get the payload, then hand over to it.

A wheel is a convenient way to *reach* cmrelay and a useless way to *be* it.
The wheel carries the CLI source and nothing else; the relay is 650 MiB of
Nix closure, and the interpreter that runs the installer has to be the one
from that closure rather than whatever pip installed it into. Shipping the
closure inside a wheel is not an option, and running the installer against
the host's Python would defeat the point of the design.

So a pip-installed cmrelay does one thing: it downloads the release payload
matching its own version and execs it. From there everything proceeds exactly
as if the operator had copied the .run file across themselves, which is the
only path that is actually tested.

    pip install cmrelay
    cmrelay install --mail-domain relay.example.org
"""

import os
import platform
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .version import __version__

RELEASES = "https://github.com/chatmail/cmrelay/releases/download"

ARCHES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
}


class FetchError(Exception):
    pass


def asset_name(version: str, arch: str) -> str:
    return f"cmrelay-{version}-{arch}-linux.run"


def payload_url(version: str = None, arch: str = None) -> str:
    version = version or __version__
    if version.startswith("0.0.0"):
        raise FetchError(
            "this cmrelay is not a released version, so there is no payload"
            " to download. Build one with"
            " `nix build '.?submodules=1#payload'` in a cmrelay checkout and"
            " run the resulting .run file directly."
        )
    machine = platform.machine()
    arch = arch or ARCHES.get(machine)
    if arch is None:
        raise FetchError(f"cmrelay has no payload for {machine}")
    return f"{RELEASES}/v{version}/{asset_name(version, arch)}"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "cmrelay"


def download(url: str, dest: Path, out) -> Path:
    """Fetch the payload to *dest*, reporting progress on a slow link."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    out.print(f"  fetching {url}")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            step = 16 * 1024 * 1024
            next_report = step
            with tmp.open("wb") as handle:
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk)
                    done += len(chunk)
                    if done >= next_report:
                        if total:
                            out.print(f"  {done // 1048576} / {total // 1048576} MiB")
                        else:
                            out.print(f"  {done // 1048576} MiB")
                        next_report += step
    except (urllib.error.URLError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"could not download {url}: {exc}") from exc

    tmp.replace(dest)
    dest.chmod(0o755)
    return dest


def ensure_payload(out, version: str = None) -> Path:
    """Return a local payload, downloading it once and caching it."""
    url = payload_url(version)
    dest = cache_dir() / url.rsplit("/", 1)[-1]
    if dest.is_file():
        out.print(f"  using cached {dest}")
        return dest
    return download(url, dest, out)


def exec_payload(payload: Path, argv: list[str]) -> int:
    """Run the payload with the original arguments. Does not return on success.

    TMPDIR matters: makeself unpacks the archive there, and /tmp is a tmpfs on
    several of the target distributions while the payload is ~180 MiB.
    """
    env = dict(os.environ)
    env.setdefault("TMPDIR", "/var/tmp")
    argv = [str(payload), "--quiet", "--", *argv]
    os.execve(str(payload), argv, env)


def run_from_wheel(args_list: list[str], out) -> int:
    """Handle a verb that needs the closure, from a CLI that has no closure."""
    if not shutil.which("tar"):
        raise FetchError("cmrelay needs tar on the host to unpack its payload")
    if sys.platform != "linux":
        raise FetchError(f"cmrelay installs relays on Linux, not {sys.platform}")
    out.section("Fetching the cmrelay payload")
    payload = ensure_payload(out)
    out.print(f"  handing over to {payload}")
    sys.stdout.flush()
    exec_payload(payload, args_list)
    return 1  # unreachable unless execve fails
