"""The extractor's value is that it fails loudly, so that is what is tested.

relay keeps its port table and its pinned binary URLs inside Python function
bodies. Parsing them out is a bridge until relay lifts them into declarative
files; the reason it is an acceptable bridge is that every extractor asserts
the shape it expects. An upstream refactor has to become a build failure
here, never silent drift.
"""

import textwrap

import pytest

from cmrelay.relayextract import (
    ShapeError,
    extract_all,
    extract_binary_pins,
    extract_port_table,
)


def write(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(textwrap.dedent(source))
    return path


def test_reads_the_real_relay(relay_root):
    data = extract_all(relay_root)
    assert len(data["port_table"]) >= 15
    assert set(data["binary_pins"]) == {"filtermail", "turn", "iroh", "mtail"}
    for arches in data["binary_pins"].values():
        assert set(arches) == {"x86_64", "aarch64"}
        for pin in arches.values():
            assert pin["url"].startswith("https://")
            assert len(pin["sha256"]) == 64


def test_port_table_shape(relay_root):
    entries = extract_port_table(relay_root / "cmdeploy/src/cmdeploy/deployers.py")
    by_port = {e.get("port"): e["names"] for e in entries if "port" in e}
    assert by_port[25] == ["master", "smtpd"]
    assert by_port[53] == ["unbound"]
    assert by_port[993] == ["dovecot", "imap-login"]

    conditional = [e for e in entries if e.get("conditional")]
    assert len(conditional) == 1
    assert conditional[0]["port"] == 402

    # The six entries relay computes from chatmail.ini rather than a literal.
    deferred = sorted(e["config_attr"] for e in entries if "config_attr" in e)
    assert deferred == [
        "filtermail_http_port_incoming",
        "filtermail_lmtp_port_transport",
        "filtermail_smtp_port",
        "filtermail_smtp_port_incoming",
        "postfix_reinject_port",
        "postfix_reinject_port_incoming",
    ]


def test_moving_the_table_behind_a_helper_is_an_error(tmp_path):
    path = write(
        tmp_path,
        "deployers.py",
        """
        def deploy_chatmail(config):
            port_services = build_port_table(config)
    """,
    )
    with pytest.raises(ShapeError, match="not a list literal"):
        extract_port_table(path)


def test_removing_the_table_is_an_error(tmp_path):
    path = write(tmp_path, "deployers.py", "def deploy_chatmail(config):\n    pass\n")
    with pytest.raises(ShapeError, match="moved the port table"):
        extract_port_table(path)


def test_a_shrunken_table_is_an_error(tmp_path):
    path = write(
        tmp_path,
        "deployers.py",
        """
        def deploy_chatmail(config):
            port_services = [(["master", "smtpd"], 25), ("unbound", 53)]
    """,
    )
    with pytest.raises(ShapeError, match="expected at least"):
        extract_port_table(path)


def test_a_port_from_the_wrong_object_is_an_error(tmp_path):
    entries = ", ".join(f'("svc{i}", {1000 + i})' for i in range(20))
    path = write(
        tmp_path,
        "deployers.py",
        f"""
        def deploy_chatmail(config):
            port_services = [{entries}]
            port_services += [("filtermail", settings.smtp_port)]
    """,
    )
    with pytest.raises(ShapeError, match="refers to settings"):
        extract_port_table(path)


def test_pins_read_from_a_subscripted_dict(tmp_path):
    path = write(
        tmp_path,
        "deployer.py",
        """
        class ThingDeployer:
            def install(self):
                (url, sha256sum) = {
                    "x86_64": ("https://example.org/thing-x86_64", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
                    "aarch64": ("https://example.org/thing-aarch64", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
                }[arch]
    """,
    )
    pins = extract_binary_pins(path)
    assert pins["thing"]["x86_64"]["url"] == "https://example.org/thing-x86_64"
    assert (
        pins["thing"]["aarch64"]["sha256"]
        == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    )


def test_pins_read_from_a_sha_only_dict_with_a_sibling_url(tmp_path):
    path = write(
        tmp_path,
        "deployer.py",
        """
        class ThingDeployer:
            def install(self):
                arch = host.get_fact(facts.server.Arch)
                url = f"https://example.org/v1/thing-{arch}"
                sha256sum = {
                    "x86_64": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "aarch64": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                }[arch]
    """,
    )
    pins = extract_binary_pins(path)
    assert pins["thing"]["x86_64"]["url"] == "https://example.org/v1/thing-x86_64"


def test_an_unexpected_url_placeholder_is_an_error(tmp_path):
    path = write(
        tmp_path,
        "deployer.py",
        """
        class ThingDeployer:
            def install(self):
                (url, sha256sum) = {
                    "x86_64": (f"https://example.org/{version}/thing", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
                    "aarch64": (f"https://example.org/{version}/thing", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
                }[arch]
    """,
    )
    with pytest.raises(ShapeError, match="unexpected placeholder"):
        extract_binary_pins(path)


def test_a_missing_component_is_an_error(tmp_path, relay_root):
    import shutil

    fake = tmp_path / "relay"
    (fake / "cmdeploy/src/cmdeploy/mtail").mkdir(parents=True)
    (fake / "cmdeploy/src/cmdeploy/filtermail").mkdir(parents=True)
    shutil.copyfile(
        relay_root / "cmdeploy/src/cmdeploy/deployers.py",
        fake / "cmdeploy/src/cmdeploy/deployers.py",
    )
    for sub in ("mtail", "filtermail"):
        (fake / f"cmdeploy/src/cmdeploy/{sub}/deployer.py").write_text("x = 1\n")

    with pytest.raises(ShapeError, match="no pins found for filtermail, mtail"):
        extract_all(fake)
