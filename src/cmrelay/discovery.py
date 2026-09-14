"""Where a newer release is, when the operator has not named a file.

GitHub is asked for the release list, and nothing more is trusted from it:
the candidate is verified against the release keys in the installed closure
before it is unpacked, let alone run.
"""

import json
import platform
import ssl
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .errors import CmrelayError
from .version import parse_version

GITHUB = "https://api.github.com/repos/chatmail/cmrelay/releases"

TIMEOUT = 10


class DiscoveryError(CmrelayError):
    pass


@dataclass
class Candidate:
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


def releases(ctx, fetch=fetch) -> list:
    """Every signed release for this architecture that GitHub lists."""
    suffix = f"-{arch()}-linux.run"
    found = []
    for release in json.loads(fetch(GITHUB, ctx)):
        if release.get("draft") or release.get("prerelease"):
            continue
        assets = {a["name"]: a for a in release.get("assets", [])}
        for name, asset in assets.items():
            if not name.endswith(suffix) or f"{name}.sig" not in assets:
                continue
            version = name.removeprefix("cmrelay-installer-").removesuffix(suffix)
            found.append(
                Candidate(
                    version,
                    asset["browser_download_url"],
                    assets[f"{name}.sig"]["browser_download_url"],
                    name,
                    asset.get("size", 0),
                )
            )
    return found


def newest(candidates) -> Candidate | None:
    ranked = [(parse_version(c.version), c) for c in candidates]
    ranked = [(v, c) for v, c in ranked if v is not None]
    if not ranked:
        return None
    return max(ranked, key=lambda pair: pair[0])[1]


def download(candidate: Candidate, ctx, target: Path, journal) -> tuple:
    """Fetch the candidate and its signature into *target*. Returns both paths."""
    journal.ensure_directory("download", target, mode=0o700)
    run = target / candidate.filename
    journal.write("download", run, fetch(candidate.url, ctx, timeout=600), mode=0o755)
    signature = target / f"{candidate.filename}.sig"
    journal.write("download", signature, fetch(candidate.signature_url, ctx))
    return run, signature
