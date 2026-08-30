"""The command surface, and the imports argparse alone would never reach."""

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from cmrelay import cli


def test_every_verb_the_parser_offers_has_a_handler():
    from cmrelay.verbs import VERBS

    parser = cli.build_parser()
    actions = [
        a for a in parser._actions if a.choices and hasattr(a, "_name_parser_map")
    ]
    offered = set(actions[0].choices)
    for name in offered:
        assert name in VERBS, name


def lazy_imports(function) -> list:
    """The `from .x import y` lines inside a function body."""
    source = textwrap.dedent(inspect.getsource(function))
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            found.append((node.module, [alias.name for alias in node.names]))
    return found


def test_the_lazily_imported_error_types_all_exist():
    """main() imports them only on the error path, so nothing else would notice."""
    import importlib

    imports = lazy_imports(cli.main)
    assert imports, "main() no longer defers its imports"
    for module, names in imports:
        loaded = importlib.import_module(f"cmrelay.{module}")
        for name in names:
            assert hasattr(loaded, name), f"cmrelay.{module}.{name}"


def test_every_module_imports():
    """A module nothing else pulls in is a module nothing type-checks."""
    import importlib

    root = Path(cli.__file__).parent
    for path in sorted(root.glob("*.py")):
        if path.stem != "__main__":
            importlib.import_module(f"cmrelay.{path.stem}")


def test_deploy_takes_an_optional_domain():
    args = cli.build_parser().parse_args(["deploy"])
    assert args.domain is None
    assert args.verb == "deploy"

    args = cli.build_parser().parse_args(["deploy", "relay.example.org"])
    assert args.domain == "relay.example.org"


def test_disable_and_enable_mail_are_the_same_switch():
    parse = cli.build_parser().parse_args
    assert parse(["deploy"]).enable_mail is None
    assert parse(["deploy", "--disable-mail"]).enable_mail is False
    assert parse(["deploy", "--enable-mail"]).enable_mail is True
    with pytest.raises(SystemExit):
        parse(["deploy", "--disable-mail", "--enable-mail"])


def test_take_over_rejects_the_flags_that_would_rewrite_the_adopted_ini(capsys):
    with pytest.raises(SystemExit):
        cli.main(["deploy", "--take-over", "--config", "/tmp/x.ini"])
    assert "do not apply" in capsys.readouterr().err


def test_uninstall_wants_the_domain_as_the_confirmation():
    args = cli.build_parser().parse_args(["uninstall", "relay.example.org"])
    assert args.domain == "relay.example.org"


def test_no_command_prints_help_and_exits_two(capsys):
    assert cli.main([]) == 2
    assert "COMMAND" in capsys.readouterr().out


def test_the_installer_verb_is_hidden_from_help():
    parser = cli.build_parser()
    assert "installer" not in parser.format_help()


def test_the_output_flags_work_on_either_side_of_the_verb():
    parse = cli.build_parser().parse_args
    assert parse(["deploy", "--verbose"]).verbose is True
    assert parse(["--verbose", "deploy"]).verbose is True
    assert parse(["-q", "deploy"]).quiet is True
    assert parse(["deploy", "-q"]).quiet is True
    assert parse(["--color", "never", "deploy"]).color == "never"
    assert parse(["deploy", "--color", "never"]).color == "never"


def test_a_no_op_deploy_reaches_the_network_never():
    from types import SimpleNamespace

    from cmrelay import fsops
    from cmrelay.verbs import wants_dns_report

    args = SimpleNamespace(website_only=False, dry_run=False, skip_dns_check=False)
    quiet = fsops.Journal()
    assert wants_dns_report(args, quiet, [], []) is False

    busy = fsops.Journal()
    busy.note(fsops.Change(Path("/etc/mailname"), "+", "postfix", True))
    assert wants_dns_report(args, busy, [], []) is True
    assert wants_dns_report(args, quiet, [["start", "nginx.service"]], []) is True

    args.skip_dns_check = True
    assert wants_dns_report(args, busy, [], []) is False


def test_the_installer_hand_over_list_matches_the_dispatch_table():
    from cmrelay.installer import VERBS as handover
    from cmrelay.verbs import VERBS as dispatch

    assert set(handover) == set(dispatch) - {"installer"}
