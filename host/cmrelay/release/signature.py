"""Whether an installer is the one the release CI signed.

Its last line is a marker and the base64 of a raw Ed25519 signature.
"""

import base64
import binascii
import hashlib
import shutil
import tempfile
from pathlib import Path

KEY = "release-key.pem"

MARK = b"cmrelay-sig "
LINE = len(MARK) + 88 + 1


def fingerprint(pem: bytes) -> str:
    """SHA256 over the key's DER body, the shape ssh-keygen prints."""
    der = base64.b64decode(b"".join(pem.strip().splitlines()[1:-1]))
    digest = base64.b64encode(hashlib.sha256(der).digest()).rstrip(b"=")
    return f"SHA256:{digest.decode()}"


def detach(installer: Path, dest: Path) -> str | None:
    """Copy *installer* to *dest*, cut off its signature line, and return the signature."""
    shutil.copyfile(installer, dest)
    with dest.open("r+b") as f:
        size = f.seek(0, 2)
        if size < LINE:
            return None
        f.seek(size - LINE)
        line = f.read(LINE)
        if not (line.startswith(MARK) and line.endswith(b"\n")):
            return None
        f.truncate(size - LINE)
    return line[len(MARK) : -1].decode("ascii", "replace")


def verify(bundle, artefact: Path, signature) -> bool:
    """Whether the release key signed these exact bytes."""
    key = bundle.public_key
    if not (key and signature):
        return False
    try:
        raw = base64.b64decode(signature, validate=True)
    except binascii.Error:
        return False
    with tempfile.NamedTemporaryFile(suffix=".sig") as sig:
        sig.write(raw)
        sig.flush()
        options = ("-verify", "-pubin", "-inkey", key, "-sigfile", sig.name)
        command = ("pkeyutl", "-rawin", "-in", artefact, *options)
        return bundle.run("openssl", *command, check=False).returncode == 0
