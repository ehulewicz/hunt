"""Every test's model call is stubbed: none may reach the real `claude` CLI.

`subprocess.run` and the asyncio subprocesses both go through
`subprocess.Popen`, so guarding it catches every path — a runner left
unstubbed fails its test, and a call made on a background thread is still
recorded and fails the test after it ends.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker.jobs import model

# argv programs a guarded Popen refused, per test.
REACHED_CLAUDE: list[str] = []


def _program(args: Any) -> str:
    """The basename of the program an argv would exec."""
    if isinstance(args, str):
        argv = shlex.split(args)
    elif isinstance(args, (bytes, os.PathLike)):
        argv = [os.fsdecode(args)]
    else:
        argv = [os.fsdecode(item) for item in args]
    return Path(argv[0]).name if argv else ""


class GuardedPopen(subprocess.Popen):
    """A Popen that refuses to launch `claude`: tests stub their runner."""

    def __init__(self, args: Any, *args_: Any, **kwargs: Any) -> None:
        if _program(args) == "claude":
            REACHED_CLAUDE.append(str(args))
            # Failed is a BaseException: an `except Exception` cannot swallow it.
            raise pytest.fail.Exception(
                f"a test reached the real claude CLI: {args!r}"
            )
        super().__init__(args, *args_, **kwargs)


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    REACHED_CLAUDE.clear()
    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
    yield
    model.end_run()
    if REACHED_CLAUDE:
        pytest.fail("a test reached the real claude CLI: stub its runner")
