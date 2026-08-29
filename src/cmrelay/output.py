"""Terminal output. Colour only when somebody is there to see it."""

import sys

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


class Out:
    def __init__(self, stream=None, colour=None):
        self.stream = stream or sys.stdout
        if colour is None:
            colour = self.stream.isatty()
        self.colour = colour

    def _write(self, text, code=""):
        if self.colour and code:
            text = f"{code}{text}{RESET}"
        print(text, file=self.stream, flush=True)

    def print(self, text=""):
        self._write(text)

    def section(self, text):
        self._write(f"\n{text}", BOLD)

    def ok(self, text):
        self._write(text, GREEN)

    def warn(self, text):
        self._write(f"warning: {text}", YELLOW)

    def error(self, text):
        self._write(f"error: {text}", RED)
