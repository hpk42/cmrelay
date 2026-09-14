"""The line protocol: marks, colour and sections."""

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


def test_changed_is_never_red_and_errors_are():
    out, stream = rendered(colour=True)
    out.changed("nginx.conf")
    assert CYAN in stream.getvalue() and RED not in stream.getvalue()
    out.error("no DKIM key")
    out.unchanged("ok")
    assert RED in stream.getvalue() and GREEN in stream.getvalue()


def test_colour_follows_the_terminal_and_no_color(monkeypatch):
    assert rendered()[0].colour is False
    stream = io.StringIO()
    stream.isatty = lambda: True
    assert Out(stream=stream).colour is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert Out(stream=stream).colour is False
    assert Out(stream=stream, colour=True).colour is True


def test_sections_are_separated_by_one_blank_line():
    out, stream = rendered(colour=False)
    out.header("Relay", [("Host", "bare"), ("Domain", ""), ("Version", "0.4.0")])
    out.section("Preflight")
    out.unchanged("ok")
    out.summary("nothing to do")
    assert stream.getvalue().splitlines() == [
        "Relay",
        "  Host        bare",
        "  Version     0.4.0",
        "",
        "Preflight",
        "    ok",
        "",
        "nothing to do",
    ]


def test_a_stream_with_an_ascii_encoding_does_not_raise():
    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    Out(stream=stream, colour=False).print("relay.exämple.org")
    stream.flush()


@pytest.mark.parametrize("kind", ["+", "~", "-"])
def test_mark_dispatches_on_the_kind(kind):
    out, stream = rendered(colour=False)
    out.mark(kind, "a")
    assert stream.getvalue().strip().startswith(kind)
