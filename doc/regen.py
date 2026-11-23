"""Keep the manual's command examples and invocations true to the code.

In a literal block whose first line is `$ cmrelay ... --help`, the lines after
each such line are that command's output, as cmrelay's own parser writes it
without colours; blocks of other commands are left as they are. A block
introduced by a line that mentions defaults gets the default of each `key = value` from
chatmaild's chatmail.ini template.
Every other `cmrelay ...` in a code block or inline literal must parse.

Without --update, what differs is printed as a diff and the exit status is 1;
with --update the files are rewritten, for `git diff` to show.
"""

import argparse
import contextlib
import difflib
import io
import os
import re
import shlex
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "host"))
from cmrelay.cli import main as cli

PAGES = sorted((REPO / "doc").glob("*.rst"))
READMES = [REPO / "README.md", REPO / "host/README.md", REPO / "installer/README.md"]
TEMPLATE = REPO / "host/chatmaild/src/chatmaild/ini/chatmail.ini.f"
REFERENCE = REPO / "doc/cmrelay.rst"


def indent(line):
    return len(line) - len(line.lstrip(" "))


def literal_blocks(lines):
    """(opener, first, end) line indices of every literal block of a page."""
    i = 0
    while i < len(lines):
        text = lines[i].strip()
        directive = text.startswith((".. code-block::", ".. code::"))
        if not (directive or (text.endswith("::") and not text.startswith(".."))):
            i += 1
            continue
        first = i + 1
        while first < len(lines) and (
            not lines[first].strip()
            or (directive and lines[first].strip().startswith(":"))
        ):
            first += 1
        if first == len(lines) or indent(lines[first]) <= indent(lines[i]):
            i = first
            continue
        depth, end = indent(lines[first]), first
        while end < len(lines) and (
            not lines[end].strip() or indent(lines[end]) >= depth
        ):
            end += 1
        while not lines[end - 1].strip():
            end -= 1
        yield i, first, end
        i = end


def parse(command):
    """What argparse says about a cmrelay command line, or None if it is fine."""
    argv = shlex.split(command)[1:]
    if argv and not argv[0].islower():
        # a placeholder such as COMMAND or ...
        return None
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            cli.build_parser().parse_args(argv)
        except SystemExit as exc:
            if exc.code:
                return stderr.getvalue().strip().splitlines()[-1]
    return None


def wants_help(command):
    words = command.split()
    return words[0] == "cmrelay" and ("-h" in words or "--help" in words)


def run(command):
    """The lines a block shows for a help command: the command and its output."""
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.suppress(SystemExit):
        cli.build_parser().parse_args(shlex.split(command)[1:])
    return [f"$ {command}", *stdout.getvalue().rstrip("\n").splitlines()]


def regenerate(path, problems):
    """The page with its blocks regenerated, collecting what does not parse."""
    lines = path.read_text().splitlines()
    inline = re.findall(r"``(cmrelay [^`]+)``", "\n".join(lines))
    commands, shown = [(path.name, command) for command in inline], []
    # in page order, as a reader would run them
    for opener, first, end in literal_blocks(lines):
        depth = indent(lines[first])
        block = [line[depth:] for line in lines[first:end]]
        where = [f"{path.name}:{n + 1}" for n in range(first, end)]
        if block[0].startswith("$ "):
            # of shown commands only the commands, not their output
            runs = [(w, line[2:]) for w, line in zip(where, block) if line[:2] == "$ "]
            commands += runs
            if all(wants_help(command) for _, command in runs):
                new = [line for _, c in runs for line in run(c)]
                shown.append((first, end, [(" " * depth + s).rstrip() for s in new]))
            continue
        commands += zip(where, block)
        if "default" in lines[opener]:
            template = TEMPLATE.read_text()
            known = dict(re.findall(r"^#?(\w+) = (.*)$", template, re.MULTILINE))
            for n, key in enumerate(line.split(" = ")[0] for line in block):
                if key not in known:
                    problems.append(f"{where[n]}: {key} is no chatmail.ini setting")
                else:
                    lines[first + n] = " " * depth + f"{key} = {known[key]}"
    for first, end, new in reversed(shown):
        lines[first:end] = new
    for where, line in commands:
        command = line.removeprefix("$ ")
        if command.startswith("cmrelay ") and (error := parse(command)):
            problems.append(f"{where}: {command}: {error}")
    return "\n".join(lines) + "\n"


def readme_problems(path):
    """The invocations of a README: in code blocks and inline code."""
    text = path.read_text()
    commands = re.findall(r"`(cmrelay [^`]+)`", text)
    commands += re.findall(r"^(?: {4,}|\t)(cmrelay .*)$", text, re.MULTILINE)
    return [f"{path.name}: {c}: {e}" for c in commands if (e := parse(c))]


def main(argv=None):
    with patch.dict(os.environ, COLUMNS="80", PYTHON_COLORS="0"):
        return check(argv)


def check(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--update", action="store_true", help="rewrite what differs")
    args = parser.parse_args(argv)
    problems, stale = [], []
    for path in PAGES:
        old, new = path.read_text(), regenerate(path, problems)
        if new == old:
            continue
        stale.append(path)
        if args.update:
            path.write_text(new)
        else:
            name = str(path.relative_to(REPO))
            diff = difflib.unified_diff(
                old.splitlines(True), new.splitlines(True), name, name
            )
            sys.stdout.writelines(diff)
    for path in READMES:
        problems += readme_problems(path)
    reference = REFERENCE.read_text()
    for name in cli.VERBS:
        if name != "installer" and f"$ cmrelay {name} --help" not in reference:
            problems.append(f"{REFERENCE.name}: no block for `cmrelay {name} --help`")
    for problem in problems:
        print(problem, file=sys.stderr)
    if stale and not args.update:
        print(
            f"stale: {len(stale)} pages; `doc/regen.py --update` rewrites them",
            file=sys.stderr,
        )
    return 1 if problems or (stale and not args.update) else 0


if __name__ == "__main__":
    sys.exit(main())
