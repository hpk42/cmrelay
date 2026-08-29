"""Where unit files live is a decision systemd makes for us.

The obvious layout -- real files under /opt/cmrelay, symlinked into
/etc/systemd/system -- does not survive `systemctl disable`, which removes a
unit that is itself a symlink in the unit search path. cmrelay disables two
units deliberately, because their timers own them, so under that layout it
would delete them. These tests pin the direction that works.
"""

import pytest

from cmrelay import paths, systemd


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """Point the module's paths at a temporary tree."""
    monkeypatch.setattr(paths, "SYSTEMD_SYSTEM", tmp_path / "etc/systemd/system")
    monkeypatch.setattr(paths, "UNITS", tmp_path / "opt/cmrelay/systemd")
    paths.SYSTEMD_SYSTEM.mkdir(parents=True)
    return tmp_path


def test_the_unit_file_is_real_and_the_index_entry_is_the_symlink(fake_root):
    assert systemd.install_unit("doveauth.service", "[Unit]\n") is True

    real = paths.SYSTEMD_SYSTEM / "doveauth.service"
    index = paths.UNITS / "doveauth.service"

    assert real.is_file() and not real.is_symlink()
    assert index.is_symlink() and index.resolve() == real
    assert real.read_text() == "[Unit]\n"


def test_reinstalling_identical_content_reports_no_change(fake_root):
    systemd.install_unit("doveauth.service", "[Unit]\n")
    assert systemd.install_unit("doveauth.service", "[Unit]\n") is False
    assert systemd.install_unit("doveauth.service", "[Unit]\nX=1\n") is True


def test_installing_over_a_mask_replaces_the_dev_null_symlink(fake_root):
    masked = paths.SYSTEMD_SYSTEM / "dovecot.service"
    masked.symlink_to("/dev/null")

    assert systemd.install_unit("dovecot.service", "[Unit]\n") is True
    assert masked.is_file() and not masked.is_symlink()


def test_installing_over_a_distribution_unit_replaces_it(fake_root):
    """Only reached after --take-over has stopped and disabled the original."""
    distro = paths.SYSTEMD_SYSTEM / "nginx.service"
    distro.write_text("# Debian's\n")

    assert systemd.install_unit("nginx.service", "# cmrelay's\n") is True
    assert distro.read_text() == "# cmrelay's\n"


def test_the_index_names_exactly_the_units_cmrelay_owns(fake_root):
    for name in ("doveauth.service", "chatmail-expire.timer"):
        systemd.install_unit(name, "[Unit]\n")
    assert sorted(p.name for p in paths.UNITS.iterdir()) == [
        "chatmail-expire.timer",
        "doveauth.service",
    ]
