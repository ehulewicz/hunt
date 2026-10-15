"""Single-key controls for the autofill poll loop.

Ctrl+S saves what has been picked up so far. Ctrl+A says the application went
in: save, mark it applied, and end. The terminal normally swallows
Ctrl+S as XOFF flow control, so the watcher turns that off while it runs and
restores the terminal on exit. Ctrl+C is left to the kernel as SIGINT so the
session's KeyboardInterrupt path handles it.
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

SAVE_KEYS = frozenset({"\x13", "s", "S"})
DONE_KEYS = frozenset({"\x01", "a", "A"})
SKIP_KEYS = frozenset({"\x04", "d", "D"})


class KeyWatcher:
    def __init__(self) -> None:
        self._fd: int | None = None
        self._saved: Any = None

    def __enter__(self) -> KeyWatcher:
        if sys.stdin.isatty():
            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            mode = termios.tcgetattr(self._fd)
            mode[0] &= ~termios.IXON
            termios.tcsetattr(self._fd, termios.TCSADRAIN, mode)
            tty.setcbreak(self._fd, termios.TCSADRAIN)
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
        """Sleep for up to `seconds`, returning the control names pressed meanwhile."""
        if self._fd is None:
            time.sleep(seconds)
            return set()
        pressed: set[str] = set()
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return pressed
            ready, _, _ = select.select([self._fd], [], [], remaining)
            if not ready:
                return pressed
            for char in os.read(self._fd, 64).decode(errors="ignore"):
                if char in SAVE_KEYS:
                    pressed.add("save")
                if char in DONE_KEYS:
                    pressed.add("done")
                if char in SKIP_KEYS:
                    pressed.add("skip")


class NoKeys:
    """Test stand-in that just sleeps."""

    def __enter__(self) -> NoKeys:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def wait(self, seconds: float) -> set[str]:
        time.sleep(seconds)
        return set()


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
