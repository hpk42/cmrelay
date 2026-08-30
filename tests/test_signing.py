"""Signature verification, including the cases that must not read as tampering."""

import base64
import shutil
import subprocess

import pytest

from cmrelay import signing

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl is not on PATH"
)


def keypair(directory, name):
    private = directory / f"{name}.key"
    public = directory / f"{name}.pem"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(private)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
        check=True,
        capture_output=True,
    )
    return private, public


class FakeBundle:
    def __init__(self, root):
        self.root = root


@pytest.fixture
def closure(tmp_path):
    (tmp_path / "release-keys").mkdir()
    return FakeBundle(tmp_path)


@pytest.fixture
def artefact(tmp_path):
    path = tmp_path / "cmrelay-installer-0.4.0-x86_64-linux.run"
    path.write_bytes(b"#!/bin/sh\n" + b"payload" * 1000)
    return path


def trust(closure, public, name="ci"):
    target = closure.root / "release-keys" / f"{name}.pem"
    target.write_bytes(public.read_bytes())
    return target


def test_a_good_signature_verifies_and_names_the_key(tmp_path, closure, artefact):
    private, public = keypair(tmp_path, "ci")
    trust(closure, public)
    verdict = signing.verify(closure, artefact, signing.sign(private, artefact, public))
    assert verdict.ok
    assert verdict.key == "ci"
    assert verdict.fingerprint.startswith("SHA256:")


def test_a_tampered_file_does_not_verify(tmp_path, closure, artefact):
    private, public = keypair(tmp_path, "ci")
    trust(closure, public)
    envelope = signing.sign(private, artefact, public)
    artefact.write_bytes(artefact.read_bytes() + b"x")
    verdict = signing.verify(closure, artefact, envelope)
    assert not verdict.ok
    assert "altered in transit" in verdict.reason


def test_a_wrong_key_does_not_verify(tmp_path, closure, artefact):
    private, public = keypair(tmp_path, "theirs")
    _, other = keypair(tmp_path, "ours")
    trust(closure, other)
    verdict = signing.verify(closure, artefact, signing.sign(private, artefact, public))
    assert not verdict.ok


def test_an_unknown_algorithm_says_so_rather_than_crying_tampering(closure, artefact):
    envelope = signing.format_envelope(
        [signing.Entry("sshsig-sk-ed25519", "SHA256:x", "AAAA")]
    )
    keypair_dir = closure.root / "release-keys"
    (keypair_dir / "ci.pem").write_text(
        "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA" + "A" * 28 + "=\n"
        "-----END PUBLIC KEY-----\n"
    )
    verdict = signing.verify(closure, artefact, envelope)
    assert not verdict.ok
    assert "sshsig-sk-ed25519" in verdict.reason
    assert "newer signing scheme" in verdict.reason
    assert verdict.unknown == ("sshsig-sk-ed25519",)


def test_two_sig_lines_verify_if_either_one_does(tmp_path, closure, artefact):
    private, public = keypair(tmp_path, "offline")
    trust(closure, public, name="offline")
    good = signing.parse_envelope(signing.sign(private, artefact, public)).entries[0]
    envelope = signing.format_envelope(
        [signing.Entry("future-scheme", "SHA256:x", "AAAA"), good]
    )
    verdict = signing.verify(closure, artefact, envelope)
    assert verdict.ok
    assert verdict.key == "offline"
    assert verdict.unknown == ("future-scheme",)


def test_the_offline_key_alone_verifies_and_is_named(tmp_path, closure, artefact):
    """A release arriving on the recovery key is what an operator must be told."""
    ci_private, ci_public = keypair(tmp_path, "ci")
    offline_private, offline_public = keypair(tmp_path, "offline")
    trust(closure, ci_public, name="ci")
    trust(closure, offline_public, name="offline")

    verdict = signing.verify(
        closure, artefact, signing.sign(offline_private, artefact, offline_public)
    )
    assert verdict.ok
    assert verdict.key == "offline"
    assert ci_private.exists()


def test_a_closure_with_no_keys_refuses(tmp_path, closure, artefact):
    private, public = keypair(tmp_path, "ci")
    envelope = signing.sign(private, artefact, public)
    (closure.root / "release-keys").rmdir()
    verdict = signing.verify(closure, artefact, envelope)
    assert not verdict.ok
    assert "no keys" in verdict.reason


def test_a_file_that_is_not_an_envelope_is_refused(closure, artefact):
    with pytest.raises(signing.SigningError, match="cmrelay-signature-v1"):
        signing.verify(closure, artefact, base64.b64encode(b"raw").decode())


def test_an_envelope_with_no_sig_line_is_refused(closure, artefact):
    with pytest.raises(signing.SigningError, match="no sig line"):
        signing.verify(closure, artefact, f"{signing.MAGIC}\nalg  ed25519\n")


def test_the_fingerprint_is_stable_and_key_specific(tmp_path):
    _, first = keypair(tmp_path, "a")
    _, second = keypair(tmp_path, "b")
    assert signing.fingerprint(first.read_bytes()) == signing.fingerprint(
        first.read_bytes()
    )
    assert signing.fingerprint(first.read_bytes()) != signing.fingerprint(
        second.read_bytes()
    )


def test_the_envelope_round_trips():
    entries = [
        signing.Entry("ed25519", "SHA256:abc", "c2ln"),
        signing.Entry("x", "", "y"),
    ]
    parsed = signing.parse_envelope(signing.format_envelope(entries))
    assert [(e.alg, e.key, e.sig) for e in parsed.entries] == [
        ("ed25519", "SHA256:abc", "c2ln"),
        ("x", "", "y"),
    ]
