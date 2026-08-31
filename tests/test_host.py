"""Host classification and domain resolution, one test per table cell."""

import pytest

from cmrelay import host
from cmrelay.preflight import Report


@pytest.fixture
def tree(tmp_path):
    """The five paths classification looks at, none of them present yet."""
    return {
        "root": tmp_path / "opt/cmrelay",
        "ini": tmp_path / "opt/cmrelay/chatmail.ini",
        "store": tmp_path / "opt/cmrelay/nix/store",
        "cmdeploy_ini": tmp_path / "usr/local/lib/chatmaild/chatmail.ini",
        "marker": tmp_path / "etc/chatmail-version",
        "units": tmp_path / "opt/cmrelay/systemd",
    }


def put(path, text="x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def populate_store(store):
    (store / "abc-thing").mkdir(parents=True)


def test_a_bare_host_is_bare(tree):
    assert host.classify(**tree).state == host.BARE


def test_an_ini_and_a_store_mean_installed(tree):
    put(tree["ini"], "[params]\nmail_domain = relay.example.org\n")
    populate_store(tree["store"])
    put(tree["marker"], "abc123\n# installed by cmrelay 0.4.0\n")

    where = host.classify(**tree)
    assert where.state == host.INSTALLED
    assert where.cmrelay_version == "0.4.0"
    assert where.description == "installed by cmrelay 0.4.0"


def test_a_store_alone_is_what_the_installer_leaves_and_is_not_partial(tree):
    populate_store(tree["store"])
    assert host.classify(**tree).state == host.BARE


def test_a_store_plus_a_unit_file_is_partial(tree, tmp_path):
    populate_store(tree["store"])
    (tmp_path / "opt/cmrelay/systemd").mkdir(parents=True)
    assert host.classify(**tree).state == host.PARTIAL


def test_the_state_directory_the_lock_creates_is_not_a_trace(tree, tmp_path):
    """fsops.lock() makes /opt/cmrelay/state before classification runs."""
    populate_store(tree["store"])
    (tmp_path / "opt/cmrelay/state").mkdir(parents=True)
    assert host.classify(**tree).state == host.BARE


def test_an_ini_without_a_store_is_partial(tree):
    put(tree["ini"], "[params]\nmail_domain = relay.example.org\n")
    assert host.classify(**tree).state == host.PARTIAL


def test_a_cmdeploy_ini_wins_over_partial(tree):
    """The installer unpacks the store first, so take-over must be reachable."""
    populate_store(tree["store"])
    put(tree["cmdeploy_ini"], "[params]\nmail_domain = relay.example.org\n")

    where = host.classify(**tree)
    assert where.state == host.CMDEPLOY
    assert where.ini == tree["cmdeploy_ini"]


def test_a_bare_version_marker_alone_means_cmdeploy(tree):
    put(tree["marker"], "cae03e2714\n")
    where = host.classify(**tree)
    assert where.state == host.CMDEPLOY
    assert where.relay_commit == "cae03e2714"


def test_a_cmrelay_marker_alone_does_not_mean_cmdeploy(tree):
    put(tree["marker"], "cae03e2714\n# installed by cmrelay 0.4.0\n")
    assert host.classify(**tree).state == host.BARE


def test_a_modified_working_tree_is_reported(tree):
    put(tree["marker"], "cae03e2714\ndiff --git a/x b/x\n")
    assert host.classify(**tree).hand_modified is True


def test_a_bare_host_with_a_conflict_is_foreign():
    report = Report()
    report.add("port 25", "held by exim4")
    assert host.escalate(host.Host(host.BARE), report).state == host.FOREIGN


def test_a_cmdeploy_host_with_a_conflict_stays_cmdeploy():
    report = Report()
    report.add("postfix.service", "already present")
    where = host.Host(host.CMDEPLOY)
    assert host.escalate(where, report).state == host.CMDEPLOY


# -- domain resolution -----------------------------------------------------


def installed(tmp_path, domain="relay.example.org"):
    ini = tmp_path / "chatmail.ini"
    ini.write_text(f"[params]\nmail_domain = {domain}\n")
    return host.Host(host.INSTALLED, ini)


def test_no_domain_on_an_installed_host_uses_the_ini(tmp_path):
    assert host.resolve_domain(installed(tmp_path), None) == "relay.example.org"


def test_a_matching_domain_proceeds(tmp_path):
    where = installed(tmp_path)
    assert host.resolve_domain(where, "relay.example.org") == "relay.example.org"


def test_a_different_domain_is_refused(tmp_path):
    with pytest.raises(host.DomainRefused, match="not relay2.example.org"):
        host.resolve_domain(installed(tmp_path), "relay2.example.org")


def test_the_refusal_names_the_uninstall_route(tmp_path):
    with pytest.raises(host.DomainRefused) as caught:
        host.resolve_domain(installed(tmp_path), "relay2.example.org")
    message = str(caught.value)
    assert "cmrelay uninstall relay.example.org" in message
    assert "--force" in message


def test_the_refusal_counts_the_mailboxes_it_would_strand(tmp_path):
    mailboxes = tmp_path / "mail"
    for name in ("a@x", "b@x", "c@x"):
        (mailboxes / name).mkdir(parents=True)
    with pytest.raises(host.DomainRefused, match="3 mailboxes"):
        host.resolve_domain(
            installed(tmp_path), "relay2.example.org", mailboxes_dir=mailboxes
        )


def test_force_accepts_a_different_domain(tmp_path):
    where = installed(tmp_path)
    assert host.resolve_domain(where, "relay2.example.org", force=True) == (
        "relay2.example.org"
    )


def test_a_bare_host_needs_a_domain():
    with pytest.raises(host.DomainRefused, match="needs a domain"):
        host.resolve_domain(host.Host(host.BARE), None)


def test_a_bare_host_takes_the_domain_it_is_given():
    assert host.resolve_domain(host.Host(host.BARE), "relay.example.org") == (
        "relay.example.org"
    )


def test_a_partial_host_with_no_ini_asks_for_the_domain():
    with pytest.raises(host.DomainRefused, match="interrupted install"):
        host.resolve_domain(host.Host(host.PARTIAL), None)


def test_a_cmdeploy_host_adopts_its_own_domain(tmp_path):
    ini = tmp_path / "chatmail.ini"
    ini.write_text("[params]\nmail_domain = old.example.org\n")
    where = host.Host(host.CMDEPLOY, ini)
    assert host.resolve_domain(where, None) == "old.example.org"


def test_a_commented_domain_is_not_read(tmp_path):
    ini = tmp_path / "chatmail.ini"
    ini.write_text("[params]\nmail_domain = relay.example.org  # was other\n")
    assert host.config_domain(ini) == "relay.example.org"


# -- the mailbox count -----------------------------------------------------


def test_the_mailbox_count_stops_at_the_cap(tmp_path):
    for index in range(25):
        (tmp_path / f"user{index}").mkdir()
    assert host.mailbox_count(tmp_path, cap=10) == (10, True)
    assert host.mailbox_count(tmp_path, cap=100) == (25, False)


def test_a_missing_mailbox_directory_counts_nothing(tmp_path):
    assert host.mailbox_count(tmp_path / "absent") == (0, False)
