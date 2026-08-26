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
    extract_port_table,
    read_pins,
)


def write(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(textwrap.dedent(source))
    return path


def test_reads_the_real_relay(relay_root):
    data = extract_all(relay_root)
    assert len(data["port_table"]) >= 15
    pins = data["pins"]
    for table in (
        "FILTERMAIL_ARTIFACTS",
        "MTAIL_ARTIFACTS",
        "TURN_ARTIFACTS",
        "IROH_ARTIFACTS",
    ):
        assert {"x86_64", "aarch64"} <= set(pins[table])
        for url, digest in pins[table].values():
            assert url.startswith("https://")
            assert len(digest) == 64


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


def test_a_pins_module_that_imports_is_refused(tmp_path):
    """Running upstream code is only acceptable while it pulls in nothing."""
    path = write(
        tmp_path,
        "pins.py",
        """
        import os

        FILTERMAIL_ARTIFACTS = {}
        MTAIL_ARTIFACTS = {}
        TURN_ARTIFACTS = {}
        IROH_ARTIFACTS = {}
    """,
    )
    with pytest.raises(ShapeError, match="imports something"):
        read_pins(path)


def test_a_missing_artifact_table_is_an_error(tmp_path):
    path = write(
        tmp_path,
        "pins.py",
        """
        FILTERMAIL_ARTIFACTS = {}
        MTAIL_ARTIFACTS = {}
    """,
    )
    with pytest.raises(ShapeError, match="TURN_ARTIFACTS, IROH_ARTIFACTS"):
        read_pins(path)


def test_values_json_cannot_express_are_dropped(tmp_path):
    """relay keeps DOVECOT_SHA256 keyed by tuples; cmrelay does not use it."""
    path = write(
        tmp_path,
        "pins.py",
        """
        FILTERMAIL_ARTIFACTS = {"x86_64": ("https://example.org/x", "a")}
        MTAIL_ARTIFACTS = {}
        TURN_ARTIFACTS = {}
        IROH_ARTIFACTS = {}
        DOVECOT_SHA256 = {("core", "amd64"): "deadbeef"}
    """,
    )
    pins = read_pins(path)
    assert "DOVECOT_SHA256" not in pins
    assert pins["FILTERMAIL_ARTIFACTS"]["x86_64"] == (
        "https://example.org/x",
        "a",
    )
