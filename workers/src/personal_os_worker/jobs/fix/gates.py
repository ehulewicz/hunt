"""The gates a candidate passes, each in a subprocess, and the probe that tries it live.

Also the fixer's workspace helpers: the interpreter and stale-workdir cleanup.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.base import Ready
from personal_os_worker.jobs.registry import policy

PROBE_TIMEOUT_SECONDS = 180
CHECK_TIMEOUT_SECONDS = 600


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


def run_probe(ready: Ready, args: dict[str, Any]) -> dict[str, Any]:
    """The probe, in its own process, checking the candidate file over CDP."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(args | {"target": ready.problem.target}, handle, default=str)
        path = Path(handle.name)
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        process = subprocess.run(
            [
                str(interpreter()), "-m", "personal_os_worker.jobs.fix.probe",
                str(path), ready.problem.category,
                *[str(c.learned_file) for c in ready.candidates],
            ],
            capture_output=True, text=True,
            timeout=PROBE_TIMEOUT_SECONDS, env=environment,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "note": f"the probe could not finish: {error}"}
    finally:
        path.unlink(missing_ok=True)
    if process.returncode != 0:
        return {"ok": False, "note": "the probe crashed: " + process.stderr.strip()[-500:]}
    lines = [line for line in process.stdout.splitlines() if line.strip().startswith("{")]
    try:
        result = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        return {"ok": False, "note": "the probe crashed: " + process.stderr.strip()[-500:]}
    if not isinstance(result, dict) or type(result.get("ok")) is not bool:
        return {"ok": False, "note": "no valid probe result"}
    return result


_ROOT = Path(__file__).resolve().parents[3]  # the workspace uv runs from


def _gate(command: list[str], name: str) -> str | None:
    """Run one gate command under the workspace; its failure text or None."""
    try:
        process = subprocess.run(
            command, cwd=_ROOT, capture_output=True, text=True,
            timeout=CHECK_TIMEOUT_SECONDS,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"{name} could not run: {error}"
    if process.returncode != 0:
        return f"{name} failed:\n{(process.stdout + process.stderr)[-3000:]}"
    return None


def check_candidate(attempt_dir: Path, learned_file: Path, problem: lane.Problem) -> str | None:
    """Every gate a candidate passes in one subprocess order; the first failure."""
    category, site = problem.category, problem.site
    violations = policy.check_file(learned_file, category=category, site=site)
    if violations:
        return "the policy refused it: " + "; ".join(violations)
    if "proven=True" in learned_file.read_text():
        return "a new or revised file stays on trial: proven must be False"
    for name, command in (
        ("ruff", ["uv", "run", "ruff", "check", str(learned_file)]),
        ("mypy", ["uv", "run", "mypy", str(learned_file)]),
        (
            "examples",
            ["uv", "run", "python", "-m", "personal_os_worker.jobs.fix.examples",
             str(learned_file), str(attempt_dir / "fixtures")],
        ),
    ):
        failed = _gate(command, name)
        if failed is not None:
            return failed
    if category in {"formats", "questions"}:
        return _gate(
            ["uv", "run", "python", "-m", "personal_os_worker.jobs.replay",
             "--json", "--learned", str(learned_file)],
            "replay",
        )
    return None
