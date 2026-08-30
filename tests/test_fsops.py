"""The journal is the only way to the filesystem, checked two ways.

An AST pass proves no other module can mutate anything, and a whole deploy
against a fake root proves the journal names every path that actually moved --
which the AST pass cannot see, because a mutation can go through fsops and
still forget to name itself.
"""

import ast
import os
from pathlib import Path

import pytest
from conftest import REPO  # noqa: I001 - added to sys.path by conftest

from cmrelay import activation, fsops

# store.py moves a 700 MiB closure by rename and reports it as one line;
# payload.py runs from a pip-installed wheel, where there is no journal;
# vendoring.py runs from scripts/vendor-sync on a developer machine and
# writes vendor/, never a relay.
ALLOWED = {"fsops.py", "store.py", "payload.py", "vendoring.py"}

# Calls on the journal itself are the point, not a violation.
JOURNALS = {"journal", "self.j", "self.journal", "d.j", "fsops.Journal()"}

MUTATING_METHODS = {
    "write_text",
    "write_bytes",
    "mkdir",
    "unlink",
    "rmdir",
    "symlink_to",
    "hardlink_to",
    "rename",
    "chmod",
    "touch",
    "lchmod",
}

MUTATING_FUNCTIONS = {
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rename"),
    ("os", "replace"),
    ("os", "chmod"),
    ("os", "chown"),
    ("os", "mkdir"),
    ("os", "makedirs"),
    ("os", "rmdir"),
    ("os", "symlink"),
    ("os", "truncate"),
    ("shutil", "chown"),
    ("shutil", "copy"),
    ("shutil", "copy2"),
    ("shutil", "copyfile"),
    ("shutil", "copytree"),
    ("shutil", "rmtree"),
    ("shutil", "move"),
}

WRITING_MODES = ("w", "a", "x", "+")


def modules():
    for path in sorted((REPO / "src/cmrelay").glob("*.py")):
        if path.name not in ALLOWED:
            yield path


def offending_calls(source: str) -> list:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            mode = ""
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = str(keyword.value.value)
            if any(letter in mode for letter in WRITING_MODES):
                found.append((node.lineno, "open(..., %r)" % mode))
        if isinstance(func, ast.Attribute):
            if ast.unparse(func.value) in JOURNALS:
                continue
            # Path.replace() takes one argument; str.replace() takes two.
            if func.attr == "replace" and len(node.args) == 1:
                found.append((node.lineno, ".replace()"))
            elif func.attr in MUTATING_METHODS:
                found.append((node.lineno, f".{func.attr}()"))
            elif isinstance(func.value, ast.Name):
                pair = (func.value.id, func.attr)
                if pair in MUTATING_FUNCTIONS:
                    found.append((node.lineno, f"{pair[0]}.{pair[1]}()"))
    return found


@pytest.mark.parametrize("module", list(modules()), ids=lambda p: p.name)
def test_only_fsops_touches_the_filesystem(module):
    found = offending_calls(module.read_text())
    assert not found, (
        f"{module.name} mutates the filesystem outside fsops.py: "
        + ", ".join(f"line {line}: {what}" for line, what in found)
    )


def test_the_ast_check_would_catch_a_new_mutation():
    assert offending_calls("from pathlib import Path\nPath('x').write_text('y')\n")
    assert offending_calls("import shutil\nshutil.rmtree('/x')\n")
    assert offending_calls("open('x', 'w')\n")
    assert offending_calls("Path('a').replace(Path('b'))\n")
    assert not offending_calls("open('x')\nPath('x').read_text()\n")
    assert not offending_calls("body.replace('a', 'b')\n")
    assert not offending_calls("self.j.chmod('c', p, 0o600)\n")


# -- the journal itself ----------------------------------------------------


def test_a_write_reports_added_then_changed_then_nothing(tmp_path):
    journal = fsops.Journal()
    target = tmp_path / "a.conf"
    assert journal.write("x", target, "one\n").kind == fsops.ADDED
    assert journal.write("x", target, "one\n") is None
    assert journal.write("x", target, "two\n").kind == fsops.CHANGED
    assert journal.seen["x"] == 3


def test_a_dry_run_records_the_change_and_performs_none(tmp_path):
    journal = fsops.Journal(dry_run=True)
    target = tmp_path / "a.conf"
    assert journal.write("x", target, "one\n").kind == fsops.ADDED
    assert not target.exists()


def test_paths_under_opt_cmrelay_are_not_system_files():
    assert fsops.is_system(Path("/etc/mailname"))
    assert fsops.is_system(Path("/usr/local/bin/doveadm"))
    assert fsops.is_system(Path("/etc/systemd/system/postfix.service"))
    assert not fsops.is_system(Path("/opt/cmrelay/config/postfix/main.cf"))


def test_only_system_paths_are_recorded_for_hand_edit_detection(tmp_path, monkeypatch):
    from cmrelay import paths

    monkeypatch.setattr(paths, "ROOT", tmp_path / "opt/cmrelay")
    journal = fsops.Journal()
    journal.write("x", tmp_path / "opt/cmrelay/own", "a\n")
    journal.write("x", tmp_path / "etc/theirs", "b\n")
    assert list(journal.wrote) == [str(tmp_path / "etc/theirs")]


def test_an_owned_file_edited_by_hand_is_replaced_and_the_original_kept(tmp_path):
    target = tmp_path / "etc/mailname"
    first = fsops.Journal()
    first.write("x", target, "one\n")

    target.write_text("edited by somebody\n")
    second = fsops.Journal(prior=first.wrote)
    change = second.write("x", target, "two\n")

    assert change.detail == "was edited by hand"
    assert target.read_text() == "two\n"
    assert (tmp_path / "etc/mailname.replaced").read_text() == "edited by somebody\n"


def test_a_guest_file_edited_by_hand_is_left_alone(tmp_path):
    target = tmp_path / "etc/journald.conf"
    first = fsops.Journal()
    first.write("x", target, "one\n")

    target.write_text("the operator's retention\n")
    second = fsops.Journal(prior=first.wrote)

    assert second.write("x", target, "two\n", guest=True) is None
    assert target.read_text() == "the operator's retention\n"
    assert second.hand_edited == [(target, "left alone")]


def test_a_file_cmrelay_never_wrote_is_not_a_hand_edit(tmp_path):
    target = tmp_path / "etc/aliases"
    target.parent.mkdir(parents=True)
    target.write_text("somebody else's\n")
    journal = fsops.Journal()
    assert journal.write("x", target, "ours\n").kind == fsops.CHANGED
    assert journal.hand_edited == []


def test_created_parent_directories_are_reported(tmp_path):
    journal = fsops.Journal()
    journal.write("x", tmp_path / "a/b/c/file", "x\n")
    created = {change.path for change in journal.changes}
    assert tmp_path / "a" in created
    assert tmp_path / "a/b" in created
    assert tmp_path / "a/b/c" in created


def test_a_removed_file_drops_out_of_the_record(tmp_path):
    target = tmp_path / "etc/gone"
    journal = fsops.Journal()
    journal.write("x", target, "x\n")
    assert journal.remove_file("x", target).kind == fsops.REMOVED
    assert journal.wrote == {}
    assert journal.remove_file("x", target) is None


def test_a_symlink_is_reported_once_and_not_again(tmp_path):
    journal = fsops.Journal()
    link, target = tmp_path / "link", tmp_path / "target"
    target.write_text("x")
    assert journal.symlink("x", link, target).kind == fsops.ADDED
    assert journal.symlink("x", link, target) is None


def test_a_mode_change_alone_counts_as_a_change(tmp_path):
    journal = fsops.Journal()
    target = tmp_path / "script"
    journal.write("x", target, "#!/bin/sh\n", mode=0o644)
    assert journal.write("x", target, "#!/bin/sh\n", mode=0o755).kind == fsops.CHANGED
    assert target.stat().st_mode & 0o777 == 0o755


def test_the_lock_refuses_a_second_holder(tmp_path):
    path = tmp_path / "lock"
    with fsops.lock(path):
        with pytest.raises(fsops.LockBusy, match=str(os.getpid())):
            with fsops.lock(path):
                pass


# -- the whole deploy, against a fake root ---------------------------------


def snapshot(root: Path) -> dict:
    """Every path under root, and enough of its state to notice a change."""
    tree = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            tree[path] = ("link", str(path.readlink()))
        elif path.is_dir():
            tree[path] = ("dir", path.stat().st_mode & 0o7777)
        else:
            tree[path] = ("file", fsops.file_digest(path), path.stat().st_mode & 0o7777)
    return tree


def differing(before: dict, after: dict) -> set:
    return {p for p in set(before) | set(after) if before.get(p) != after.get(p)}


@pytest.fixture
def deployment(fake_root, bundle, monkeypatch, tmp_path):
    import grp
    import pwd
    import subprocess
    from types import SimpleNamespace

    import pytest as _pytest

    chatmaild = _pytest.importorskip("chatmaild.config")
    from cmrelay import paths, users
    from cmrelay.deploy import Deployment
    from cmrelay.output import Out

    # Ownership is the host's, and this test is not root.
    monkeypatch.setattr(pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.getuid()))
    monkeypatch.setattr(grp, "getgrnam", lambda _: SimpleNamespace(gr_gid=os.getgid()))
    monkeypatch.setattr(users, "ensure_group", lambda name: False)
    monkeypatch.setattr(users, "verify_mailbox_ownership", lambda *a, **k: None)

    inipath = tmp_path / "chatmail.ini"
    chatmaild.write_initial_config(inipath, "relay.example.org", {})
    config = chatmaild.read_config(inipath)
    config.tls_cert_mode = "self"
    config.mailboxes_dir = str(fake_root / "home/vmail/mail/relay.example.org")
    # From the ini rather than from paths.py, so the fixture redirects them.
    config.tls_cert_path = str(fake_root / "var/lib/acme/live/relay/cert")
    config.tls_key_path = str(fake_root / "var/lib/acme/live/relay/privkey")

    newemail = tmp_path / "newemail.py"
    newemail.write_text(
        '#!/usr/bin/python3\nCONFIG_PATH = "/usr/local/lib/chatmaild/chatmail.ini"\n'
    )

    def shell(self, *argv, **kwargs):
        """openssl is the only shell-out whose output later steps read."""
        argv = [str(a) for a in argv]
        for flag in ("-out", "-keyout"):
            if flag in argv:
                target = Path(argv[argv.index(flag) + 1])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("stub\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    deployment = Deployment(bundle, config, Out(colour=False), fsops.Journal())
    monkeypatch.setattr(Deployment, "shell", shell)
    monkeypatch.setattr(
        Deployment, "find_chatmaild_resource", lambda self, name: newemail
    )
    paths.INI.parent.mkdir(parents=True, exist_ok=True)
    paths.INI.write_text(inipath.read_text())
    return deployment


def test_the_journal_covers_every_path_a_deploy_changes(deployment, fake_root):
    before = snapshot(fake_root)
    deployment.configure()
    activation.install_daemon_units(deployment)
    after = snapshot(fake_root)

    moved = differing(before, after)
    assert moved, "the deploy changed nothing, so this proves nothing"
    unreported = moved - deployment.j.paths
    assert not unreported, sorted(str(p) for p in unreported)


def test_a_second_deploy_against_the_same_root_changes_nothing(deployment, fake_root):
    deployment.configure()
    activation.install_daemon_units(deployment)

    before = snapshot(fake_root)
    deployment.j = fsops.Journal(prior=deployment.j.wrote)
    deployment.configure()
    activation.install_daemon_units(deployment)
    after = snapshot(fake_root)

    assert differing(before, after) == set()
    assert deployment.j.changes == []
