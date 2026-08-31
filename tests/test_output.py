"""The line protocol: marks, colour, durations and the quiet mode."""

import io

import pytest

from cmrelay.output import CYAN, GREEN, RED, Out


def rendered(**kwargs):
    stream = io.StringIO()
    return Out(stream=stream, **kwargs), stream


def test_the_marks_say_what_moved():
    out, stream = rendered(colour=False)
    out.added("a.conf")
    out.changed("b.conf")
    out.removed("c.conf")
    out.unchanged("41 files unchanged")
    assert stream.getvalue().splitlines() == [
        "  + a.conf",
        "  ~ b.conf",
        "  - c.conf",
        "    41 files unchanged",
    ]


def test_changed_is_never_red():
    out, stream = rendered(colour=True)
    out.changed("nginx.conf")
    assert CYAN in stream.getvalue()
    assert RED not in stream.getvalue()


def test_errors_are_red_and_unchanged_is_green():
    out, stream = rendered(colour=True)
    out.error("no DKIM key")
    out.unchanged("ok")
    assert RED in stream.getvalue()
    assert GREEN in stream.getvalue()


def test_no_colour_is_the_default_off_a_terminal():
    out, _ = rendered()
    assert out.colour is False


def test_no_color_wins_over_auto(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    stream.isatty = lambda: True
    assert Out(stream=stream).colour is False


def test_always_wins_over_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert Out(stream=io.StringIO(), colour="always").colour is True


def test_a_phase_carries_its_duration_on_the_header():
    out, stream = rendered(colour=False)
    with out.step("Preflight"):
        out.detail("no conflicting units")
    lines = stream.getvalue().splitlines()
    assert lines[0] == ""
    assert lines[1].startswith("Preflight")
    assert lines[1].rstrip().endswith("s")
    assert lines[2] == "    no conflicting units"


def test_every_section_is_separated_by_exactly_one_blank_line():
    out, stream = rendered(colour=False)
    out.header("Relay", [("Host", "bare"), ("Domain", "relay.example.org")])
    for title in ("Preflight", "Configuration"):
        with out.step(title):
            out.unchanged("ok")
    out.section_header("System files", 0.01)
    out.unchanged("nothing changed")

    lines = stream.getvalue().splitlines()
    assert lines == [
        "Relay",
        "  Host        bare",
        "  Domain      relay.example.org",
        "",
        lines[4],
        "    ok",
        "",
        lines[7],
        "    ok",
        "",
        lines[10],
        "    nothing changed",
    ]
    for index in (4, 7, 10):
        assert lines[index].rstrip().endswith("s")


def test_a_slow_phase_prints_live_rather_than_holding_its_lines():
    out, stream = rendered(colour=False, live_after=-1)
    with out.step("Services"):
        out.changed("postfix.service: start")
        assert "postfix.service" in stream.getvalue()
    assert stream.getvalue().splitlines()[:2] == ["", "Services"]


def test_quiet_prints_only_warnings_errors_and_the_summary():
    out, stream = rendered(colour=False, quiet=True)
    with out.step("Configuration"):
        out.changed("postfix   12 files")
        out.warn("acme_email is empty")
    out.summary("nothing to do")
    printed = stream.getvalue()
    assert "12 files" not in printed
    assert "acme_email" in printed
    assert "nothing to do" in printed


def test_the_header_is_titled_and_skips_empty_values():
    out, stream = rendered(colour=False)
    out.header("Relay", [("Host", "bare"), ("Domain", ""), ("Version", "0.4.0")])
    assert [line for line in stream.getvalue().splitlines() if line] == [
        "Relay",
        "  Host        bare",
        "  Version     0.4.0",
    ]


def test_a_stream_with_an_ascii_encoding_does_not_raise():
    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    out = Out(stream=stream, colour=False)
    out.print("relay.exämple.org")
    stream.flush()


@pytest.mark.parametrize("kind", ["+", "~", "-"])
def test_mark_dispatches_on_the_kind(kind):
    out, stream = rendered(colour=False)
    out.mark(kind, "a")
    assert stream.getvalue().strip().startswith(kind)
