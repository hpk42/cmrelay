"""Terminal output: what changed, what was already right, and what is wrong.

The marks are the protocol: `~` changed, `+` added, `-` removed, nothing for
unchanged. Changed is never red; a wall of red trains the reader to stop
looking.
"""

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
        self.colour = colour
        # LANG may be unset in the closure, leaving stdout ASCII, and a
        # non-ASCII domain name would then raise mid-deploy.
        reconfigure = getattr(self.stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    def _emit(self, line, code=""):
        if self.colour and code:
            line = f"{code}{line}{RESET}"
        print(line, file=self.stream, flush=True)

    def print(self, text=""):
        self._emit(text)

    def header(self, title, pairs):
        self._emit(title, BOLD)
        for name, value in pairs:
            if value:
                self._emit(f"  {name:<12}{value}")

    def section(self, title):
        self._emit("")
        self._emit(title, BOLD)

    def changed(self, text):
        self._emit(f"  ~ {text}", CYAN)

    def added(self, text):
        self._emit(f"  + {text}", CYAN)

    def removed(self, text):
        self._emit(f"  - {text}", CYAN)

    def mark(self, kind, text):
        {"+": self.added, "~": self.changed, "-": self.removed}[kind](text)

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

    def confirm(self, question) -> bool:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")

    def summary(self, text):
        self._emit("")
        self._emit(text, GREEN)
