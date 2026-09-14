"""The command surface."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmrelay import cli, fsops
from cmrelay.errors import CmrelayError
from cmrelay.verbs import VERBS, wants_dns_report


def test_every_verb_the_parser_offers_has_a_handler():
    parser = cli.build_parser()
    actions = [
        a for a in parser._actions if a.choices and hasattr(a, "_name_parser_map")
    ]
    assert set(actions[0].choices) == set(VERBS)


def test_every_module_imports():
    """A module nothing else pulls in is a module nothing type-checks."""
    import importlib

    root = Path(cli.__file__).parent
    for path in sorted(root.glob("*.py")):
        if path.stem != "__main__":
            importlib.import_module(f"cmrelay.{path.stem}")


def test_deploy_takes_an_optional_domain():
    parse = cli.build_parser().parse_args
    assert parse(["deploy"]).domain is None
    assert parse(["deploy", "relay.example.org"]).domain == "relay.example.org"


def test_disable_and_enable_mail_are_the_same_switch():
    parse = cli.build_parser().parse_args
    assert parse(["deploy"]).enable_mail is None
    assert parse(["deploy", "--disable-mail"]).enable_mail is False
    assert parse(["deploy", "--enable-mail"]).enable_mail is True
    with pytest.raises(SystemExit):
        parse(["deploy", "--disable-mail", "--enable-mail"])


def test_uninstall_wants_the_domain_as_the_confirmation():
    args = cli.build_parser().parse_args(["uninstall", "relay.example.org"])
    assert args.domain == "relay.example.org"


def test_the_installer_verb_takes_the_stubs_flags_and_hides_from_help():
    parser = cli.build_parser()
    args = parser.parse_args(["installer", "-y", "--stage-only"])
    assert args.yes and args.stage_only
    assert "installer" not in parser.format_help()


def test_no_command_prints_help_and_exits_two(capsys):
    assert cli.main([]) == 2
    assert "COMMAND" in capsys.readouterr().out


def test_a_refusal_is_one_message_and_exit_one(monkeypatch, capsys):
    def refuse(args, out):
        raise CmrelayError("nothing doing")

    monkeypatch.setitem(cli.VERBS, "version", refuse)
    assert cli.main(["version"]) == 1
    assert "error: nothing doing" in capsys.readouterr().out


def test_a_no_op_deploy_reaches_the_network_never():
    args = SimpleNamespace(skip_dns_check=False)
    quiet = fsops.Journal()
    assert wants_dns_report(args, quiet, [], []) is False

    busy = fsops.Journal()
    busy.note(fsops.Change(Path("/etc/mailname"), "+", "postfix", True))
    assert wants_dns_report(args, busy, [], []) is True
    assert wants_dns_report(args, quiet, [["start", "nginx.service"]], []) is True

    args.skip_dns_check = True
    assert wants_dns_report(args, busy, [], []) is False
