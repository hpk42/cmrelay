"""Terminal output: what changed, what was already right, and how long it took.

The marks are the protocol: `~` changed, `+` added, `-` removed, nothing for
unchanged. Changed is never red; a wall of red trains the reader to stop
looking.

Durations sit on the phase header, so a phase's lines are held back until its
duration is known. A phase running longer than LIVE_AFTER gives up and prints
live, with its duration on a closing line.
"""

import os
import sys
import time

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

# The column the duration is right-aligned to.
WIDTH = 74

# How long a phase may hold its output back before it starts printing live.
LIVE_AFTER = 2.0


class Phase:
    def __init__(self, title):
        self.title = title
        self.started = time.monotonic()
        self.lines = []
        self.live = False

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


class Out:
    def __init__(self, stream=None, colour=None, verbose=False, quiet=False):
        self.stream = stream or sys.stdout
        self.verbose = verbose
        self.quiet = quiet
        self.phase = None
        self.colour = self._want_colour(colour)
        self._make_encodable()

    def _want_colour(self, colour) -> bool:
        """auto, always or never, with NO_COLOR winning over auto."""
        if colour in ("always", True):
            return True
        if colour in ("never", False):
            return False
        if os.environ.get("NO_COLOR"):
            return False
        return bool(getattr(self.stream, "isatty", lambda: False)())

    def _make_encodable(self):
        """LANG may be unset in the closure, leaving stdout ASCII.

        A non-ASCII domain name would then raise mid-deploy.
        """
        reconfigure = getattr(self.stream, "reconfigure", None)
        if reconfigure is None:
            return
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    # -- primitives --------------------------------------------------------

    def _paint(self, text, code):
        return f"{code}{text}{RESET}" if (self.colour and code) else text

    def _emit(self, line, code="", important=False):
        if self.quiet:
            if not important:
                return
            print(self._paint(line, code), file=self.stream, flush=True)
            return
        if self.phase is None:
            print(self._paint(line, code), file=self.stream, flush=True)
            return
        if not self.phase.live and self.phase.elapsed > LIVE_AFTER:
            self._go_live()
        if self.phase.live:
            print(self._paint(line, code), file=self.stream, flush=True)
        else:
            self.phase.lines.append((line, code))

    def _go_live(self):
        phase = self.phase
        phase.live = True
        self.phase = None
        self._emit("")
        self._emit(phase.title, BOLD)
        for line, code in phase.lines:
            self._emit(line, code)
        self.phase = phase

    # -- phases ------------------------------------------------------------

    def step(self, title):
        return _Step(self, title)

    def _open(self, title):
        self.phase = Phase(title)

    def _close(self):
        phase, self.phase = self.phase, None
        if phase is None:
            return 0.0
        if phase.live:
            self._emit(f"{'':<{WIDTH}}{phase.elapsed:.2f}s", DIM)
            return phase.elapsed
        header = f"{phase.title:<{WIDTH}}{phase.elapsed:.2f}s"
        if not self.quiet:
            print(file=self.stream)
            print(self._paint(header, BOLD), file=self.stream, flush=True)
            for line, code in phase.lines:
                print(self._paint(line, code), file=self.stream, flush=True)
        return phase.elapsed

    # -- the line protocol -------------------------------------------------

    def print(self, text=""):
        self._emit(text)

    def header(self, title, pairs):
        """The preamble: what this host is, and what it will be.

        Titled like a phase and without a duration, since it is not one.
        """
        self._emit(title, BOLD)
        for name, value in pairs:
            if value:
                self._emit(f"  {name:<12}{value}")

    def section_header(self, title, seconds: float):
        """A phase whose duration was measured somewhere other than a step."""
        self._emit("")
        self._emit(f"{title:<{WIDTH}}{seconds:.2f}s", BOLD)

    def section(self, text):
        self._emit(f"\n{text}", BOLD)

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
        self._emit(f"warning: {text}", YELLOW, important=True)

    def error(self, text):
        self._emit(f"error: {text}", RED, important=True)

    def confirm(self, question) -> bool:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")

    def summary(self, text):
        self._emit("", important=True)
        self._emit(text, GREEN, important=True)

    def next_step(self, text):
        self._emit("", important=True)
        self._emit(f"Next: {text}", important=True)


class _Step:
    def __init__(self, out, title):
        self.out = out
        self.title = title
        self.elapsed = 0.0

    def __enter__(self):
        self.out._open(self.title)
        return self

    def __exit__(self, *exc):
        self.elapsed = self.out._close()
        return False
