"""The wheel builder, whose job is to make `pip install` the last online step.

Two of these guard against bugs that shipped in deltachat-rpc-server, which
packages a binary into a wheel the same way: a WHEEL file whose Tag field is
a literal `{tag}` placeholder, and a version containing `-` that produces a
filename pip refuses to install.
"""

import base64
import csv
import hashlib
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def load_script():
    path = ROOT / "scripts" / "wheel-cmrelay.py"
    spec = importlib.util.spec_from_file_location("wheel_cmrelay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return load_script()


@pytest.fixture
def built(script, tmp_path):
    payload = tmp_path / "cmrelay.run"
    payload.write_bytes(b"#!/bin/sh\necho payload\n")
    out = tmp_path / "dist"
    return script.build(payload, "x86_64", "1.2.3", ROOT / "src", ROOT, out)


def test_the_tag_field_is_not_a_placeholder(script):
    """The bug every published deltachat-rpc-server wheel carries."""
    text = script.wheel_metadata(["py3-none-manylinux_2_17_x86_64"])
    assert "{tag}" not in text
    assert "Tag: py3-none-manylinux_2_17_x86_64" in text


def test_one_tag_line_per_platform(script):
    """The filename compresses the tags with dots; WHEEL must not."""
    tags = [f"py3-none-{t}" for t in script.ARCH_TAGS["x86_64"]]
    lines = [
        ln for ln in script.wheel_metadata(tags).splitlines() if ln.startswith("Tag:")
    ]
    assert lines == [
        "Tag: py3-none-manylinux_2_17_x86_64",
        "Tag: py3-none-manylinux2014_x86_64",
    ]


def test_every_arch_produces_at_least_one_tag(script):
    for arch, tags in script.ARCH_TAGS.items():
        assert tags, arch
        # Alpine is refused by the installer, so the tag must not be claimed.
        assert not any("musllinux" in t for t in tags), arch


def test_a_version_with_a_dash_is_refused(script):
    """`2.60.0-dev` parses as a build tag, which must start with a digit."""
    with pytest.raises(SystemExit, match="build tag"):
        script.check_version("1.2.3-dev")
    assert script.check_version("1.2.3.dev0") == "1.2.3.dev0"


def test_the_payload_is_executable_in_the_zip(built, script):
    """pip restores the mode from the zip, and the CLI execs this file."""
    with zipfile.ZipFile(built) as zf:
        info = zf.getinfo(script.PAYLOAD_MEMBER)
    assert (info.external_attr >> 16) & 0o777 == 0o755


def test_the_payload_is_stored_not_deflated(built, script):
    """It is already a zstd stream; deflating it again buys nothing."""
    with zipfile.ZipFile(built) as zf:
        assert zf.getinfo(script.PAYLOAD_MEMBER).compress_type == zipfile.ZIP_STORED


def test_the_filename_carries_the_compressed_tag(built):
    assert built.name == (
        "cmrelay-1.2.3-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
    )


def test_record_hashes_every_entry(built):
    with zipfile.ZipFile(built) as zf:
        name = next(n for n in zf.namelist() if n.endswith("/RECORD"))
        rows = list(csv.reader(io.StringIO(zf.read(name).decode())))
        hashed = 0
        for path, digest, size in rows:
            if not digest:
                assert path == name  # RECORD cannot hash itself
                continue
            data = zf.read(path)
            want = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
            assert digest == f"sha256={want.decode()}"
            assert int(size) == len(data)
            hashed += 1
    assert hashed == len(rows) - 1


def test_the_cli_is_in_there_and_declares_its_entry_point(built):
    with zipfile.ZipFile(built) as zf:
        names = zf.namelist()
        assert "cmrelay/cli.py" in names
        entry = next(n for n in names if n.endswith("entry_points.txt"))
        assert "cmrelay = cmrelay.cli:main" in zf.read(entry).decode()


def test_the_wheel_declares_no_dependencies(built):
    """Everything jinja2, markdown and qrcode do happens inside the closure."""
    with zipfile.ZipFile(built) as zf:
        name = next(n for n in zf.namelist() if n.endswith("/METADATA"))
        assert "Requires-Dist" not in zf.read(name).decode()
