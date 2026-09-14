"""Whether a candidate release is one this host will accept.

The trust root is the installed generation: the release public keys ship
inside the closure at `<closure>/release-keys/*.pem`, and `upgrade` verifies a
candidate against them before it is ever executed.

The signature is an envelope rather than 64 raw bytes, so a host meeting a
future scheme can say which algorithm it does not know instead of reporting
tampering. Repeated `sig` lines are allowed and any one of them verifying is
enough, which is what lets one release carry both the CI key and the offline
key.

    cmrelay-signature-v1
    alg  ed25519
    key  SHA256:0f3a9c...
    sig  bXlSZWxlYXNlU2lnbmF0dXJl...
"""

import base64
import hashlib
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .bundle import Bundle, BundleError
from .errors import CmrelayError

MAGIC = "cmrelay-signature-v1"
ALG = "ed25519"
KEYS_DIR = "release-keys"

_PEM_BODY = re.compile(
    r"-----BEGIN PUBLIC KEY-----(.*?)-----END PUBLIC KEY-----", re.DOTALL
)


class SigningError(CmrelayError):
    pass


@dataclass
class Entry:
    alg: str
    key: str
    sig: str


@dataclass
class Envelope:
    entries: list = field(default_factory=list)

    @property
    def algorithms(self) -> set:
        return {entry.alg for entry in self.entries}


@dataclass
class Verdict:
    ok: bool
    key: str = ""
    fingerprint: str = ""
    reason: str = ""
    unknown: tuple = ()


def fingerprint(pem: bytes | str) -> str:
    """SHA256 over the key's DER body, the shape ssh-keygen prints."""
    text = pem.decode() if isinstance(pem, bytes) else pem
    match = _PEM_BODY.search(text)
    if not match:
        raise SigningError("not a PEM public key")
    der = base64.b64decode("".join(match.group(1).split()))
    digest = base64.b64encode(hashlib.sha256(der).digest()).rstrip(b"=")
    return f"SHA256:{digest.decode()}"


def keys_dir(bundle) -> Path:
    return bundle.root / KEYS_DIR


def trusted_keys(bundle) -> list:
    """(path, fingerprint) for every key this closure accepts."""
    directory = keys_dir(bundle)
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("*.pem")):
        try:
            found.append((path, fingerprint(path.read_bytes())))
        except (OSError, SigningError):
            continue
    return found


def parse_envelope(text: str) -> Envelope:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != MAGIC:
        raise SigningError(f"not a cmrelay signature: expected {MAGIC!r} on line one")
    entries, current = [], {}
    for line in lines[1:]:
        key, _, value = line.partition(" ")
        value = value.strip()
        if key == "alg":
            if current.get("sig"):
                entries.append(Entry(**current))
                current = {}
            current["alg"] = value
        elif key == "key":
            current["key"] = value
        elif key == "sig":
            current.setdefault("alg", ALG)
            current.setdefault("key", "")
            current["sig"] = value
            entries.append(Entry(**current))
            current = {}
    if not entries:
        raise SigningError("the signature carries no sig line")
    return Envelope(entries)


def format_envelope(entries) -> str:
    lines = [MAGIC]
    for entry in entries:
        lines += [f"alg  {entry.alg}", f"key  {entry.key}", f"sig  {entry.sig}"]
    return "\n".join(lines) + "\n"


def verify(bundle, artefact: Path, envelope_text: str) -> Verdict:
    """Whether any trusted key signed these exact bytes."""
    envelope = parse_envelope(envelope_text)
    keys = trusted_keys(bundle)
    if not keys:
        return Verdict(
            False, reason=f"this closure carries no keys in {keys_dir(bundle)}"
        )

    understood = [e for e in envelope.entries if e.alg == ALG]
    unknown = tuple(sorted(envelope.algorithms - {ALG}))
    if not understood:
        return Verdict(
            False,
            reason=(
                f"this cmrelay understands {ALG} and the signature uses"
                f" {', '.join(unknown)}. That is a newer signing scheme, not"
                " a damaged file."
            ),
            unknown=unknown,
        )

    for entry in understood:
        for path, print_ in keys:
            if entry.key and entry.key != print_:
                continue
            if check(path, artefact, entry.sig):
                return Verdict(True, key=path.stem, fingerprint=print_, unknown=unknown)
    return Verdict(
        False,
        reason=(
            "no trusted key verifies this file. Either it was altered in"
            " transit, or it was signed by a key this release does not carry."
        ),
        unknown=unknown,
    )


def check(public_pem: Path, artefact: Path, signature_b64: str) -> bool:
    try:
        raw = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError):
        return False
    with tempfile.NamedTemporaryFile(suffix=".sig") as handle:
        handle.write(raw)
        handle.flush()
        result = subprocess.run(
            [
                openssl(),
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(public_pem),
                "-rawin",
                "-in",
                str(artefact),
                "-sigfile",
                handle.name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    return result.returncode == 0


def sign(private_pem: Path, artefact: Path, public_pem: Path) -> str:
    """The envelope for one artefact, for the release workflow and the tests."""
    with tempfile.NamedTemporaryFile(suffix=".sig") as handle:
        result = subprocess.run(
            [
                openssl(),
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private_pem),
                "-rawin",
                "-in",
                str(artefact),
                "-out",
                handle.name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise SigningError(f"openssl could not sign: {result.stderr.strip()}")
        raw = Path(handle.name).read_bytes()
    entry = Entry(
        ALG, fingerprint(Path(public_pem).read_bytes()), base64.b64encode(raw).decode()
    )
    return format_envelope([entry])


def openssl() -> str:
    """The closure's openssl when there is one, the host's otherwise."""
    try:
        return Bundle.discover().bin("openssl")
    except (BundleError, KeyError):
        return "openssl"
