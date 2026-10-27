"""The store moves under /opt/cmrelay without any byte changing its offset."""

import importlib.util
import os
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "relocate", Path(__file__).resolve().parent.parent / "nix/relocate.py"
)
relocate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relocate)

GLIBC = "0d8g8n0a11v6f5m2h416ajyxmnkwc3md-glibc-2.42"
POSTFIX = "bpg4c4g2mspp82b18pqxz78xq34m45wi-postfix-3.11.6"
NEW_GLIBC = "/opt/cmrelay/nix/store/0d8g8n0a11v6f5m2h416-glibc-2.42"


def test_a_reference_keeps_its_length():
    old = f"\0/nix/store/{GLIBC}/lib/ld-linux-x86-64.so.2\0".encode()
    new = relocate.relocate_bytes(old)
    assert new == f"\0{NEW_GLIBC}/lib/ld-linux-x86-64.so.2\0".encode()
    assert len(new) == len(old)
    # remove-references-to's placeholder names nothing, and moves all the same
    dead = b"/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-coreutils"
    assert relocate.relocate_bytes(dead) == (
        b"/opt/cmrelay/nix/store/eeeeeeeeeeeeeeeeeeee-coreutils"
    )


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


def test_names_contents_and_links_all_move(staged):
    assert relocate.relocate_store(staged) == []
    postfix = staged / "bpg4c4g2mspp82b18pqx-postfix-3.11.6"
    assert sorted(p.name for p in staged.iterdir()) == [
        "0d8g8n0a11v6f5m2h416-glibc-2.42",
        postfix.name,
    ]
    assert f"{NEW_GLIBC}/lib/ld.so".encode() in (postfix / "bin/postfix").read_bytes()
    assert os.readlink(postfix / "lib") == f"{NEW_GLIBC}/lib"


def test_a_hash_without_the_store_prefix_is_reported(staged):
    (staged / GLIBC / "lib/notes").write_bytes(
        f"see {POSTFIX}\0configured in /build/{POSTFIX}\0".encode()
    )
    assert relocate.relocate_store(staged) == [
        "0d8g8n0a11v6f5m2h416-glibc-2.42/lib/notes names bpg4c4g2mspp82b18pqxz78xq34m45wi"
    ]
