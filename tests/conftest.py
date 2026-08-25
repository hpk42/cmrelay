"""Shared fixtures. Tests run without root and without a container."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


@pytest.fixture(scope="session")
def relay_root():
    """The pinned relay checkout the vendor tree was generated from."""
    path = REPO / "cmdeploy-relay"
    if not (path / "cmdeploy").is_dir():
        pytest.skip("the cmdeploy-relay submodule is not checked out")
    return path


@pytest.fixture
def bundle(tmp_path):
    """A closure laid out the way nix/closure.nix lays one out."""
    import json
    import shutil

    from cmrelay.bundle import Bundle

    root = tmp_path / "nix/store/deadbeef-cmrelay-closure"
    root.mkdir(parents=True)
    shutil.copytree(REPO / "vendor", root / "vendor")
    shutil.copytree(REPO / "units", root / "units")
    shutil.copyfile(REPO / "vendor.lock", root / "vendor.lock")
    shutil.copyfile(REPO / "nix/generated/relay-pins.json", root / "relay-pins.json")

    # nix/closure.nix writes this; Bundle.command() reads it to find the
    # loader that runs a store binary before the store is installed.
    glibc = tmp_path / "nix/store/aaa-glibc-2.42/lib"
    glibc.mkdir(parents=True)
    (glibc / "ld-linux-x86-64.so.2").write_text("")
    (root / "bootstrap.env").write_text(
        "CMRELAY_LD=/nix/store/aaa-glibc-2.42/lib/ld-linux-x86-64.so.2\n"
        "CMRELAY_PYTHON=/nix/store/lll-python/bin/python3.14\n"
        "CMRELAY_SITE=/nix/store/lll-python/lib/python3.14/site-packages\n"
    )

    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "components": {
                    "postfix": {
                        "path": "/nix/store/aaa-postfix",
                        "version": "3.11.6",
                        "postfix": "/nix/store/aaa-postfix/bin/postfix",
                        "postconf": "/nix/store/aaa-postfix/bin/postconf",
                        "postmap": "/nix/store/aaa-postfix/bin/postmap",
                        "postalias": "/nix/store/aaa-postfix/bin/postalias",
                        "daemon_directory": "/nix/store/aaa-postfix/libexec/postfix",
                        "command_directory": "/nix/store/aaa-postfix/bin",
                        "meta_directory": "/nix/store/aaa-postfix/etc/postfix",
                        "shlib_directory": "no",
                        "manpage_directory": "/nix/store/aaa-postfix/share/man",
                    },
                    "dovecot": {
                        "path": "/nix/store/bbb-dovecot",
                        "version": "2.3.21",
                        "dovecot": "/nix/store/bbb-dovecot/sbin/dovecot",
                        "doveconf": "/nix/store/bbb-dovecot/bin/doveconf",
                        "doveadm": "/nix/store/bbb-dovecot/bin/doveadm",
                        "module_dir": "/nix/store/bbb-dovecot/lib/dovecot",
                        "dh_params": "/nix/store/bbb-dovecot/share/dovecot/dh.pem",
                    },
                    "nginx": {
                        "path": "/nix/store/ccc-nginx",
                        "version": "1.30.4",
                        "nginx": "/nix/store/ccc-nginx/bin/nginx",
                        "prefix": "/nix/store/ccc-nginx",
                    },
                    "unbound": {
                        "path": "/nix/store/ddd-unbound",
                        "version": "1.25.2",
                        "unbound": "/nix/store/ddd-unbound/bin/unbound",
                        "unbound-checkconf": "/nix/store/ddd-unbound/bin/unbound-checkconf",
                        "unbound-anchor": "/nix/store/ddd-unbound/bin/unbound-anchor",
                    },
                    "opendkim": {
                        "path": "/nix/store/eee-opendkim",
                        "version": "2.11.0",
                        "opendkim": "/nix/store/eee-opendkim/bin/opendkim",
                        "opendkim-genkey": "/nix/store/eee-opendkim/bin/opendkim-genkey",
                    },
                    "fcgiwrap": {
                        "path": "/nix/store/fff-fcgiwrap",
                        "version": "1.1.0",
                        "fcgiwrap": "/nix/store/fff-fcgiwrap/sbin/fcgiwrap",
                    },
                    "acmetool": {
                        "path": "/nix/store/ggg-acmetool",
                        "version": "0.2.2",
                        "acmetool": "/nix/store/ggg-acmetool/bin/acmetool",
                    },
                    "filtermail": {
                        "path": "/nix/store/hhh-filtermail",
                        "version": "0.7.4",
                        "filtermail": "/nix/store/hhh-filtermail/bin/filtermail",
                    },
                    "chatmail-turn": {
                        "path": "/nix/store/iii-turn",
                        "version": "0.4",
                        "chatmail-turn": "/nix/store/iii-turn/bin/chatmail-turn",
                    },
                    "iroh-relay": {
                        "path": "/nix/store/jjj-iroh",
                        "version": "0.35.0",
                        "iroh-relay": "/nix/store/jjj-iroh/bin/iroh-relay",
                    },
                    "mtail": {
                        "path": "/nix/store/kkk-mtail",
                        "version": "3.0.8",
                        "mtail": "/nix/store/kkk-mtail/bin/mtail",
                    },
                    "python": {
                        "path": "/nix/store/lll-python",
                        "version": "3.14.7",
                        "python": "/nix/store/lll-python/bin/python3",
                        "doveauth": "/nix/store/lll-python/bin/doveauth",
                        "chatmail-metadata": "/nix/store/lll-python/bin/chatmail-metadata",
                        "chatmail-expire": "/nix/store/lll-python/bin/chatmail-expire",
                        "chatmail-quota-expire": "/nix/store/lll-python/bin/chatmail-quota-expire",
                        "chatmail-fsreport": "/nix/store/lll-python/bin/chatmail-fsreport",
                        "lastlogin": "/nix/store/lll-python/bin/lastlogin",
                    },
                    "coreutils": {"path": "/nix/store/mmm-coreutils", "version": "9.8"},
                    "openssl": {
                        "path": "/nix/store/nnn-openssl",
                        "version": "3.6.3",
                        "openssl": "/nix/store/nnn-openssl/bin/openssl",
                    },
                    "iproute2": {
                        "path": "/nix/store/ooo-iproute2",
                        "version": "6.19",
                        "ss": "/nix/store/ooo-iproute2/bin/ss",
                    },
                },
            }
        )
    )
    return Bundle(Path("/nix/store/deadbeef-cmrelay-closure"), prefix=tmp_path)
