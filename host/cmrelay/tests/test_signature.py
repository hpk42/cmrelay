"""Signature verification, including the cases that must not read as tampering."""

import importlib.util
import json
import shutil
import subprocess
from importlib.machinery import SourceFileLoader
from types import SimpleNamespace

import pytest
from conftest import REPO

from cmrelay import CmrelayError
from cmrelay.cli.upgrade import check_signature
from cmrelay.release import signature
from cmrelay.release.bundle import Bundle

loader = SourceFileLoader("sign", str(REPO / "installer/scripts/3_sign-installer"))
sign_tool = importlib.util.module_from_spec(
    importlib.util.spec_from_loader("sign", loader)
)
loader.exec_module(sign_tool)
sign = sign_tool.sign


def keypair(directory, name):
    private = directory / f"{name}.key"
    public = directory / f"{name}.pem"
    for args in (
        ["genpkey", "-algorithm", "ed25519", "-out", private],
        ["pkey", "-in", private, "-pubout", "-out", public],
    ):
        subprocess.run(["openssl", *args], check=True, capture_output=True)
    return private, public


@pytest.fixture
def closure(tmp_path):
    """A closure whose openssl is the one on PATH."""
    packages = {"openssl": {"version": "3", "openssl": shutil.which("openssl")}}
    manifest = {"version": "1", "packages": packages}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return Bundle(tmp_path)


@pytest.fixture
def artefact(tmp_path):
    path = tmp_path / "cmrelay-installer-0.4.0-x86_64-linux.run"
    path.write_bytes(b"#!/bin/sh\n" + b"payload" * 1000)
    return path


def trust(closure, public):
    target = closure.root / signature.KEY
    target.write_bytes(public.read_bytes())
    return target


def signed_part(installer):
    copy = installer.with_suffix(".copy")
    return copy, signature.detach(installer, copy)


def test_a_tampered_file_or_other_key_does_not_verify(tmp_path, closure, artefact, out):
    private, public = keypair(tmp_path, "release")
    stranger, other = keypair(tmp_path, "other")
    original = artefact.read_bytes()
    # there is one release key, so signing again replaces the signature
    sign(stranger, artefact)
    sign(private, artefact)
    assert len(artefact.read_bytes()) == len(original) + signature.LINE
    assert signed_part(artefact)[0].read_bytes() == original
    trust(closure, other)
    assert not signature.verify(closure, *signed_part(artefact))
    trust(closure, public)
    assert signature.verify(closure, *signed_part(artefact))
    artefact.write_bytes(b"x" + artefact.read_bytes())
    assert not signature.verify(closure, *signed_part(artefact))
    # a signature that fails is refused
    args = SimpleNamespace(no_verify=False)
    with pytest.raises(CmrelayError, match="altered"):
        check_signature(closure, *signed_part(artefact), args, out)


def test_no_signature_or_no_key_verifies_nothing(tmp_path, closure, artefact, out):
    copy, sig = signed_part(artefact)
    assert sig is None and copy.read_bytes() == artefact.read_bytes()
    assert not signature.verify(closure, copy, sig)
    with pytest.raises(CmrelayError, match="--no-verify"):
        check_signature(closure, copy, sig, SimpleNamespace(no_verify=False), out)
    no_verify = SimpleNamespace(no_verify=True)
    assert check_signature(closure, copy, sig, no_verify, out) == "none"

    private, _ = keypair(tmp_path, "release")
    sign(private, artefact)
    assert closure.public_key is None
    assert not signature.verify(closure, *signed_part(artefact))
