"""The terminal during an apply run: a typed line is a direction for the fixer.

Nothing typed here can mark, skip, or save a run, because a letter typed for
something else once recorded an application as submitted. Save, Submitted, and Skip
are the Hunt page's; `os hunt apply <row> --done` records an application you sent
yourself. Ctrl+C is left to the kernel as SIGINT, so the session's KeyboardInterrupt
path stops and records the run.
"""

from __future__ import annotations

import os
import select
import sys
import termios
import time
import tty
from types import TracebackType
from typing import Any


class Terminal:
    """Holds the terminal for a run: a stray Ctrl+S must not freeze the run's output."""

    def __init__(self) -> None:
        self._fd: int | None = None
        self._saved: Any = None

    def __enter__(self) -> Terminal:
        if sys.stdin.isatty():
            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            mode = termios.tcgetattr(self._fd)
            mode[0] &= ~termios.IXON
            termios.tcsetattr(self._fd, termios.TCSADRAIN, mode)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._fd is not None and self._saved is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def wait(self, seconds: float) -> set[str]:
        """Sleep; a terminal presses no controls, so there are none to return."""
        time.sleep(seconds)
        return set()


class NoKeys:
    """Test stand-in that just sleeps."""

    def __enter__(self) -> NoKeys:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def wait(self, seconds: float) -> set[str]:
        time.sleep(seconds)
        return set()


def typed_line() -> str:
    """A line typed at the terminal and ended with Enter, if one waits; it never blocks."""
    if not sys.stdin.isatty():
        return ""
    fd = sys.stdin.fileno()
    ready, _, _ = select.select([fd], [], [], 0)
    return os.read(fd, 4096).decode(errors="ignore").strip() if ready else ""


def read_key(prompt: str) -> str:
    """One keypress (Enter counts), or a typed line when stdin is not a terminal."""
    print(prompt, end="", flush=True)
    if not sys.stdin.isatty():
        return input()
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd, termios.TCSADRAIN)
        char = os.read(fd, 1).decode(errors="ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    print()
    return char
