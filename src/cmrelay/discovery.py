"""Where a newer release might be, when the operator has not named a file.

Both GitHub and PyPI are asked, because reachability differs by region and
neither is dependably available everywhere. Neither is authority: whatever
answers, the candidate is verified against the release keys in the installed
closure before it is unpacked, let alone run, so a compromised index can
waste bandwidth and nothing else.
"""

import json
import platform
import ssl
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .version import DEV, parse_version

GITHUB = "https://api.github.com/repos/chatmail/cmrelay/releases"
PYPI = "https://pypi.org/pypi/cmrelay/json"

TIMEOUT = 10

# Inside the wheel, beside the payload.
WHEEL_PAYLOAD = "cmrelay/_payload/cmrelay.run"
WHEEL_SIGNATURE = "cmrelay/_payload/cmrelay.run.sig"


class DiscoveryError(Exception):
    pass


@dataclass
class Candidate:
    source: str
    version: str
    url: str
    signature_url: str
    filename: str
    size: int = 0


def arch() -> str:
    machine = platform.machine()
    return "aarch64" if machine in ("aarch64", "arm64") else "x86_64"


def context(ca_path: str):
    """The closure's Python has no CA bundle; the host's has to be named."""
    directory = Path(ca_path)
    if not directory.is_dir():
        raise DiscoveryError(
            f"{directory} does not exist, so there is nothing to verify TLS"
            " against. Download the installer by hand and pass it to"
            " `cmrelay upgrade FILE`."
        )
    ctx = ssl.create_default_context(capath=str(directory))
    bundle = directory / "ca-certificates.crt"
    if bundle.is_file():
        ctx.load_verify_locations(cafile=str(bundle))
    return ctx


def fetch(url: str, ctx, timeout: int = TIMEOUT) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "cmrelay"})
    with urllib.request.urlopen(request, timeout=timeout, context=ctx) as response:
        return response.read()


def from_github(ctx, fetch=fetch) -> list:
    suffix = f"-{arch()}-linux.run"
    releases = json.loads(fetch(GITHUB, ctx))
    found = []
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        assets = {a["name"]: a for a in release.get("assets", [])}
        for name, asset in assets.items():
            if not name.endswith(suffix) or f"{name}.sig" not in assets:
                continue
            version = name.removeprefix("cmrelay-installer-").removesuffix(suffix)
            found.append(
                Candidate(
                    "GitHub",
                    version,
                    asset["browser_download_url"],
                    assets[f"{name}.sig"]["browser_download_url"],
                    name,
                    asset.get("size", 0),
                )
            )
    return found


def from_pypi(ctx, fetch=fetch) -> list:
    tag = f"manylinux_2_17_{arch()}"
    data = json.loads(fetch(PYPI, ctx))
    found = []
    for version, files in data.get("releases", {}).items():
        for entry in files:
            name = entry.get("filename", "")
            if not name.endswith(".whl") or tag not in name:
                continue
            found.append(
                Candidate("PyPI", version, entry["url"], "", name, entry.get("size", 0))
            )
    return found


def best(candidates) -> Candidate | None:
    ranked = [(parse_version(c.version), c) for c in candidates]
    ranked = [(v, c) for v, c in ranked if v is not None]
    if not ranked:
        return None
    return max(ranked, key=lambda pair: pair[0])[1]


def survey(ca_path: str, source=None, fetch=fetch) -> tuple:
    """(per-source best, failures). Both are asked unless one is pinned."""
    ctx = context(ca_path)
    readers = {"GitHub": from_github, "PyPI": from_pypi}
    if source:
        readers = {n: r for n, r in readers.items() if n.lower() == source}
    results, failures = {}, {}
    for name, reader in readers.items():
        try:
            found = best(reader(ctx, fetch))
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            failures[name] = str(exc)
            continue
        if found is not None:
            results[name] = found
    return results, failures


def download(candidate: Candidate, ca_path: str, target: Path, journal) -> tuple:
    """Fetch the candidate and its signature into *target*. Returns both paths."""
    ctx = context(ca_path)
    journal.ensure_directory("download", target, mode=0o700)
    if candidate.filename.endswith(".whl"):
        return unpack_wheel(fetch(candidate.url, ctx, timeout=600), target, journal)
    run = target / candidate.filename
    journal.write("download", run, fetch(candidate.url, ctx, timeout=600), mode=0o755)
    signature = target / f"{candidate.filename}.sig"
    journal.write("download", signature, fetch(candidate.signature_url, ctx))
    return run, signature


def unpack_wheel(data: bytes, target: Path, journal) -> tuple:
    archive = target / "candidate.whl"
    journal.write("download", archive, data)
    with zipfile.ZipFile(archive) as wheel:
        names = set(wheel.namelist())
        if WHEEL_PAYLOAD not in names:
            raise DiscoveryError(f"the wheel carries no {WHEEL_PAYLOAD}")
        if WHEEL_SIGNATURE not in names:
            raise DiscoveryError(
                f"the wheel carries no {WHEEL_SIGNATURE}, so nothing can be"
                " verified against the release keys"
            )
        run = target / "cmrelay.run"
        journal.write("download", run, wheel.read(WHEEL_PAYLOAD), mode=0o755)
        signature = target / "cmrelay.run.sig"
        journal.write("download", signature, wheel.read(WHEEL_SIGNATURE))
    return run, signature


def report(out, installed: str, results: dict, failures: dict) -> None:
    out.print(f"  {'installed':<12}{installed or DEV}")
    for name, candidate in sorted(results.items()):
        size = f"{candidate.size / (1024 * 1024):.1f} MiB" if candidate.size else ""
        out.print(f"  {name:<12}{candidate.version:<8}{candidate.filename:<52}{size}")
    for name, reason in sorted(failures.items()):
        out.warn(f"{name} could not be reached: {reason}")
