"""Shared fixtures. Tests run without root and without a container."""

import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from chatmaild.config import read_config, write_initial_config

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "host"))

from cmrelay import paths
from cmrelay.cli.output import Out
from cmrelay.release.bundle import Bundle
from cmrelay.stack.deployment import COMPONENTS, Deployment
from cmrelay.system import fsops, systemd


def fake_tool(tmp_path, name, script):
    """A real executable that prints canned output, standing in for a daemon."""
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    return str(path)


@pytest.fixture(autouse=True)
def systemctl(tmp_path, monkeypatch):
    """A systemctl on PATH that logs its calls and answers `show`.

    In every test, so that none reaches the host's systemd. Call it with the
    "LoadState ActiveState UnitFileState" every unit is in, and the units
    `--failed` lists; it returns the log.
    """

    def install(show="loaded active enabled", failed=()):
        load, active, enabled = show.split()
        record = f"Id=%s\\nLoadState={load}\\nActiveState={active}\\n"
        record += f"UnitFileState={enabled}\\n\\n"
        fake_tool(
            tmp_path,
            "systemctl",
            f'echo "$@" >> {log}\n'
            f'[ "$1" != show ] || {{ shift 3; for u; do printf "{record}" "$u"; done; }}\n'
            f'[ "$1" != list-units ] || for u in {" ".join(failed)}; do echo "$u x"; done',
        )
        return log

    log = tmp_path / "systemctl-calls"
    log.touch()
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(systemd, "available", lambda: True)
    install()
    return install


@pytest.fixture
def out():
    # sys.stdout as it is at each write: capsys swaps it after setup
    stream = SimpleNamespace(write=lambda t: sys.stdout.write(t), flush=lambda: None)  # noqa: PLW0108
    return Out(stream, colour=False)


@pytest.fixture
def ini(tmp_path):
    path = tmp_path / "chatmail.ini"
    write_initial_config(path, "relay.example.org", {})
    return path


# A stand-in for the manifest installer/nix/closure.nix generates:
# each package and its executables, relative to its store path.
PACKAGES = """
postfix        bin/postfix bin/postconf bin/postmap
dovecot        sbin/dovecot bin/doveconf bin/doveadm
nginx          bin/nginx
unbound        bin/unbound bin/unbound-anchor
opendkim       bin/opendkim
fcgiwrap       sbin/fcgiwrap
acmetool       bin/acmetool
filtermail     bin/filtermail
chatmail-turn  bin/chatmail-turn
iroh-relay     bin/iroh-relay
python         bin/doveauth bin/chatmail-metadata bin/lastlogin
python         bin/chatmail-expire bin/chatmail-quota-expire bin/chatmail-fsreport
ldns           bin/drill
openssl        bin/openssl
"""


@pytest.fixture
def bundle(tmp_path):
    """A closure laid out the way installer/nix/closure.nix lays one out."""
    root = tmp_path / "lib/store/deadbeef-cmrelay-closure"
    root.mkdir(parents=True)
    skip = shutil.ignore_patterns("tests", "__pycache__")
    shutil.copytree(REPO / "host/cmrelay", root / "cmrelay", ignore=skip)
    shutil.copy(REPO / "release-key.pem", root)

    packages = {}
    for name, *tools in map(str.split, PACKAGES.strip().splitlines()):
        path = f"/nix/store/{name}"
        entry = packages.setdefault(name, {"path": path, "version": "1"})
        entry.update({Path(t).name: f"{path}/{t}" for t in tools})
    packages["dovecot"]["dh_params"] = "/nix/store/dovecot/share/dovecot/dh.pem"
    packages["nginx"]["prefix"] = "/nix/store/nginx"
    packages["python"]["python"] = "/nix/store/python/bin/python3"
    manifest = {"version": "1.0", "packages": packages}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return Bundle(Path("/nix/store/deadbeef-cmrelay-closure"), prefix=tmp_path)


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """Every path in paths.py and the components, redirected under a throwaway tree."""
    root = tmp_path / "root"
    root.mkdir()

    def under(path):
        return root / str(path).lstrip("/")

    for module in (paths, *COMPONENTS):
        for name in filter(str.isupper, dir(module)):
            value = getattr(module, name)
            if isinstance(value, Path):
                monkeypatch.setattr(module, name, under(value))
            elif isinstance(value, list) and value and isinstance(value[0], Path):
                monkeypatch.setattr(module, name, [under(p) for p in value])
    return root


@pytest.fixture
def deployment(bundle, ini, out):
    return Deployment(bundle, read_config(ini), out, fsops.Changes())
