"""The parts that make the installer: relocation and the header."""

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HEADER = HERE / "header.sh"

spec = importlib.util.spec_from_file_location("relocate", HERE / "nix/relocate.py")
relocate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relocate)

GLIBC = "0d8g8n0a11v6f5m2h416ajyxmnkwc3md-glibc-2.42"
POSTFIX = "bpg4c4g2mspp82b18pqxz78xq34m45wi-postfix-3.11.6"
NEW_GLIBC = "/opt/cmrelay/lib/store/0d8g8n0a11v6f5m2h416-glibc-2.42"


@pytest.fixture
def staged(tmp_path):
    store = tmp_path / "nix/store"
    (store / GLIBC / "lib").mkdir(parents=True)
    (store / GLIBC / "lib/libc.so.6").write_bytes(b"\x7fELF\0")
    (store / POSTFIX / "bin").mkdir(parents=True)
    (store / POSTFIX / "bin/postfix").write_bytes(
        f"\x7fELF\0/nix/store/{GLIBC}/lib/ld.so\0".encode()
    )
    (store / POSTFIX / "lib").symlink_to(f"/nix/store/{GLIBC}/lib")
    return store


def test_names_contents_and_links_all_move_and_keep_their_length(staged):
    binary = staged / POSTFIX / "bin/postfix"
    size = binary.stat().st_size
    assert relocate.relocate_store(staged) == []
    postfix = staged / "bpg4c4g2mspp82b18pqx-postfix-3.11.6"
    assert sorted(p.name for p in staged.iterdir()) == [
        "0d8g8n0a11v6f5m2h416-glibc-2.42",
        postfix.name,
    ]
    assert f"{NEW_GLIBC}/lib/ld.so".encode() in (postfix / "bin/postfix").read_bytes()
    assert (postfix / "bin/postfix").stat().st_size == size
    # remove-references-to's placeholder names nothing, and moves all the same
    dead = b"/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-coreutils"
    assert relocate.relocate_bytes(dead) == (
        b"/opt/cmrelay/lib/store/eeeeeeeeeeeeeeeeeeee-coreutils"
    )
    assert os.readlink(postfix / "lib") == f"{NEW_GLIBC}/lib"


def test_a_hash_without_the_store_prefix_is_reported(staged):
    (staged / GLIBC / "lib/notes").write_bytes(
        f"see {POSTFIX}\0configured in /build/{POSTFIX}\0".encode()
    )
    assert relocate.relocate_store(staged) == [
        "0d8g8n0a11v6f5m2h416-glibc-2.42/lib/notes names bpg4c4g2mspp82b18pqxz78xq34m45wi"
    ]


def test_the_header_refuses_arguments_before_it_unpacks():
    """Otherwise a mistyped domain costs a full unpack to be told so."""

    def header(*args):
        argv = ["sh", str(HEADER), *args]
        return subprocess.run(argv, capture_output=True, text=True, check=False)

    refused = header("relay.example.org")
    assert refused.returncode == 1
    assert "takes no arguments" in refused.stderr
    assert "cmrelay init DOMAIN" in refused.stderr
    # --stage-only passes the flag check and reaches the root test.
    allowed = header("--stage-only")
    assert "takes no arguments" not in allowed.stderr
    assert "must run as root" in allowed.stderr
    helped = header("--help")
    assert helped.returncode == 0 and "cmrelay init DOMAIN" in helped.stdout
