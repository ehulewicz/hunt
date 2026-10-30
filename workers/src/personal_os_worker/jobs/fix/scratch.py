"""Workspace helpers for the fixer: the interpreter and stale-workdir cleanup.

The fixer writes candidate files into the state directory and checks them in
one subprocess with the workspace interpreter — no scratch clone any more.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def interpreter(repo: Path | None = None) -> Path:
    """The workspace interpreter, which has the worker's test and lint tools."""
    candidates: list[Path] = [Path(sys.executable)]
    if repo is not None:
        candidates = [repo / ".venv/bin/python", repo / "workers/.venv/bin/python",
                      *candidates]
    for candidate in candidates:
        if Path(candidate).exists():
            return Path(candidate)
    return Path(sys.executable)


def clear_stale(root: Path) -> None:
    """Remove every attempt folder a previous run's fixer left under `root`;
    a missing root is fine."""
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
