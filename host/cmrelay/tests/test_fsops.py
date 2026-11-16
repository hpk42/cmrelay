"""The changes, and a whole deploy against a fake root."""

import grp
import os
import pwd
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmrelay import CmrelayError, paths
from cmrelay.stack.opendkim import opendkim
from cmrelay.stack.unbound import unbound
from cmrelay.system import fsops


def test_the_lock_refuses_a_second_holder(tmp_path):
    path = tmp_path / "lock"
    with fsops.lock(path):
        with pytest.raises(CmrelayError, match=str(os.getpid())):
            with fsops.lock(path):
                pass


def test_a_planted_link_beside_a_written_file_redirects_nothing(tmp_path):
    victim, dest = tmp_path / "victim", tmp_path / "keys/key.private"
    victim.write_text("untouched\n")
    dest.parent.mkdir()
    (dest.parent / "key.private.cmrelay-new").symlink_to(victim)
    fsops.Changes().write("opendkim", dest, "secret\n", mode=0o600)
    assert victim.read_text() == "untouched\n"
    assert dest.read_text() == "secret\n" and not dest.is_symlink()
    # nor does a link put where the file was, whose target holds the same bytes
    dest.unlink()
    victim.write_text("secret\n")
    victim.chmod(0o644)
    dest.symlink_to(victim)
    fsops.Changes().write("opendkim", dest, "secret\n", mode=0o600)
    assert not dest.is_symlink() and victim.stat().st_mode & 0o777 == 0o644


def snapshot(root):
    """Every path under root, and enough of its state to notice a change."""
    tree = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            tree[path] = ("link", str(path.readlink()))
        elif path.is_dir():
            tree[path] = ("dir", path.stat().st_mode & 0o7777)
        else:
            digest = fsops.file_digest(path)
            tree[path] = ("file", digest, path.stat().st_mode & 0o7777)
    return tree


def differing(before, after):
    return {p for p in set(before) | set(after) if before.get(p) != after.get(p)}


def test_deploy_records_every_change_then_is_a_noop(deployment, fake_root, monkeypatch):
    # Ownership is the host's, and this test is not root.
    monkeypatch.setattr(pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.getuid()))
    monkeypatch.setattr(grp, "getgrnam", lambda _: SimpleNamespace(gr_gid=os.getgid()))
    config = deployment.config
    config.tls_cert_mode = "self"
    config.mailboxes_dir = str(fake_root / "home/vmail/mail/relay.example.org")
    # From the ini rather than from paths.py, so the fixture redirects them.
    config.tls_cert_path = str(fake_root / "var/lib/acme/live/relay/cert")
    config.tls_key_path = str(fake_root / "var/lib/acme/live/relay/privkey")

    def run(self, tool, *argv, **kwargs):
        """The closure's tools, as far as later steps read what they made."""
        argv = [str(a) for a in argv]
        for flag in ("-out", "-keyout"):
            if flag in argv:
                Path(argv[argv.index(flag) + 1]).write_text("stub\n")
        return subprocess.CompletedProcess(argv, 0, "stub\n", "")

    monkeypatch.setattr(type(deployment.bundle), "run", run)
    unbound.RESOLV_CONF.parent.mkdir(parents=True, exist_ok=True)
    unbound.RESOLV_CONF.symlink_to("../run/systemd/resolve/stub-resolv.conf")
    unbound.NETPLAN_WAIT_ONLINE.parent.mkdir(parents=True)
    unbound.NETPLAN_WAIT_ONLINE.write_text(
        "[Service]\nExecStart=\nExecStart=w --any --dns -i eth0\n"
    )
    before = snapshot(fake_root)
    deployment.configure()
    after = snapshot(fake_root)
    # uninstall puts it back
    assert str(unbound.RESOLV_CONF_SAVED.readlink()).endswith("stub-resolv.conf")
    assert not unbound.RESOLV_CONF.is_symlink()
    shadow = (
        paths.SYSTEMD_SYSTEM / "systemd-networkd-wait-online.service.d/10-netplan.conf"
    )
    assert shadow.read_text().endswith("ExecStart=w --any -i eth0\n")

    moved = differing(before, after)
    reported = {c.path for c in deployment.changes}
    # a new directory counts through what is written into it
    reported |= {parent for path in reported for parent in path.parents}
    unreported = moved - reported
    assert not unreported, sorted(str(p) for p in unreported)
    # what uninstall finds and removes
    systemd_files = {p for p in moved if paths.SYSTEMD_SYSTEM in p.parents}
    systemd_files = {p for p in systemd_files if after[p][0] == "file"}
    assert systemd_files and all(map(paths.written_by_cmrelay, systemd_files))
    secrets = (config.tls_key_path, opendkim.KEYS / "opendkim.private")
    modes = [after[Path(p)][-1] for p in (*secrets, config.mailboxes_dir)]
    assert modes == [0o600, 0o600, 0o700]

    deployment.changes = fsops.Changes()
    deployment.configure()
    assert differing(after, snapshot(fake_root)) == set()
    assert not deployment.changes


def test_a_secret_is_never_wider_than_its_mode_on_the_way(tmp_path, monkeypatch):
    """The mode is set on the temporary, before the rename makes it visible."""
    seen = []
    replace = os.replace

    def watching(src, dest):
        seen.append(Path(src).stat().st_mode & 0o777)
        replace(src, dest)

    monkeypatch.setattr(os, "replace", watching)
    fsops.Changes().write("x", tmp_path / "key", "secret\n", mode=0o600)
    assert seen == [0o600]
