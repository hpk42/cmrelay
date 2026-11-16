"""Terminal output."""

import os
import sys

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"


class Out:
    def __init__(self, stream=None, colour=None, verbose=False):
        self.stream = stream or sys.stdout
        self.verbose = verbose
        if colour is None:
            isatty = getattr(self.stream, "isatty", lambda: False)
            colour = not os.environ.get("NO_COLOR") and bool(isatty())
            colour = colour or bool(os.environ.get("FORCE_COLOR"))
        self.colour = colour
        self.heading = None
        # LANG may be unset, and a non-ASCII domain would raise on an ASCII stdout.
        reconfigure = getattr(self.stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    def _emit(self, line, code=""):
        if self.heading:
            heading, self.heading = self.heading, None
            self._emit("")
            self._emit(heading, BOLD)
        if self.colour and code:
            # leading blank lines stay outside, so no line holds a bare colour code
            text = line.lstrip("\n")
            line = f"{line[: len(line) - len(text)]}{code}{text}{RESET}"
        try:
            print(line, file=self.stream, flush=True)
        except BrokenPipeError:
            # `cmrelay upgrade | head`: the reader left, the work goes on.
            os.dup2(os.open(os.devnull, os.O_WRONLY), self.stream.fileno())

    def print(self, text=""):
        self._emit(text)

    def section(self, title):
        """A heading, printed once a line follows it: an empty section says nothing."""
        self.heading = title

    def changed(self, text):
        self._emit(f"  ~ {text}", CYAN)

    def unchanged(self, text):
        self._emit(f"    {text}", GREEN)

    def detail(self, text):
        self._emit(f"    {text}", DIM)

    def ok(self, text):
        self._emit(text, GREEN)

    def warn(self, text):
        self._emit(f"warning: {text}", YELLOW)

    def error(self, text):
        self._emit(f"error: {text}", RED)

    def confirm(self, question, yes=False) -> bool:
        """Whether to go ahead; *yes* answers for the operator, and a no is said."""
        if yes:
            return True
        try:
            answer = input(f"{question} [y/N] ")
        except EOFError:
            self.print("")
            answer = ""
        if answer.strip().lower() in ("y", "yes"):
            return True
        self.print("aborted; nothing was modified")
        return False
