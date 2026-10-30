"""The one way the worker runs `claude -p`, on the maintainer's subscription.

Every call site builds its argv here and runs it through `run`, so each one is
metered (`claude_usage`), and a usage limit or failed envelope raises
`ModelError` instead of passing for an empty answer. Fill-time calls get no
tools. No lane passes tools: every call answers text only
scratch clone of the repository, never the live checkout.

Each call names its purpose and is counted under it, process-wide. Learning
(`short_answers.py`) and fixing (`fix/`) are what a site should stop needing
once it has been seen; checking and drafting run on every application.

While a run is active (`begin_run` / `end_run`, set by the apply session),
learn, check, and draft are capped per run (`CAPS`); fix is uncapped. A spent
cap raises `BudgetSpent` before anything spawns, so a purpose that ran out is
said once and the rest keep working. Caps count across a reload (`base`), and
nothing is capped outside a run.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from personal_os_worker.jobs import claude_usage
from personal_os_worker.jobs.model_errors import ModelError, checked_output

TIMEOUT_SECONDS = 300

Purpose = Literal["learn", "check", "draft", "fix"]
PURPOSES: tuple[Purpose, ...] = ("learn", "check", "draft", "fix")

# Each purpose's calls while one run is active; "fix" has no cap.
CAPS: dict[str, int] = {"learn": 20, "check": 30, "draft": 10}

# What a spent cap means for the run, one message per capped purpose.
_WHAT_RUNS_OUT: dict[str, str] = {
    "learn": "this run has made its {cap} learning calls; "
             "questions no pattern decides are left for you",
    "check": "this run has made its {cap} answer checks; "
             "answers not yet checked are listed as unchecked",
    "draft": "this run has made its {cap} drafting calls; "
             "prose with no approved writing is left for you",
}

TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


class BudgetSpent(ModelError):
    """This run hit its cap for one purpose; the process never spawned."""

    def __init__(self, purpose: Purpose) -> None:
        self.purpose = purpose
        super().__init__(_WHAT_RUNS_OUT[purpose].format(cap=CAPS[purpose]))


_calls: Counter[str] = Counter()
_calls_lock = threading.Lock()
_tokens: dict[str, Counter[str]] = {purpose: Counter() for purpose in PURPOSES}

# Calls and tokens at the last begin_run; None while no run is active.
_run_calls: dict[str, int] | None = None
_run_tokens: dict[str, Counter[str]] | None = None
_run_base: dict[str, int] = {}


def begin_run(base: Mapping[str, int] | None = None) -> None:
    """Start this run's counting; `base` credits calls a prior process made."""
    global _run_calls, _run_tokens, _run_base
    with _calls_lock:
        _run_calls = {purpose: _calls[purpose] for purpose in PURPOSES}
        _run_tokens = {purpose: Counter(_tokens[purpose]) for purpose in PURPOSES}
        _run_base = {
            purpose: int((base or {}).get(purpose, 0)) for purpose in PURPOSES
        }


def end_run() -> None:
    """No run is active; calls are uncapped until the next begin_run."""
    global _run_calls, _run_tokens, _run_base
    with _calls_lock:
        _run_calls = None
        _run_tokens = None
        _run_base = {}


def run_counts() -> dict[str, int]:
    """This run's calls per purpose (base included); process counts with no run."""
    with _calls_lock:
        if _run_calls is None:
            return {purpose: _calls[purpose] for purpose in PURPOSES}
        return {
            purpose: _run_base.get(purpose, 0) + _calls[purpose] - _run_calls[purpose]
            for purpose in PURPOSES
        }


def remaining(purpose: Purpose) -> int | None:
    """Calls left under this run's cap; None when uncapped (fix, or no run)."""
    if purpose not in CAPS:
        return None
    with _calls_lock:
        if _run_calls is None:
            return None
        used = _run_base.get(purpose, 0) + _calls[purpose] - _run_calls[purpose]
    return max(0, CAPS[purpose] - used)


def run_usage() -> dict[str, dict[str, int]]:
    """This run's calls and summed tokens per purpose."""
    with _calls_lock:
        usage: dict[str, dict[str, int]] = {}
        for purpose in PURPOSES:
            calls = (
                _calls[purpose]
                if _run_calls is None
                else _run_base.get(purpose, 0) + _calls[purpose] - _run_calls[purpose]
            )
            sums: dict[str, int] = {"calls": calls}
            for key in TOKEN_KEYS:
                total = _tokens[purpose][key]
                if _run_tokens is not None:
                    total -= _run_tokens[purpose][key]
                sums[key] = total
            usage[purpose] = sums
        return usage


def _reserve(purpose: Purpose) -> None:
    """Count the call before it spawns; refuse once this run's cap is spent."""
    with _calls_lock:
        if _run_calls is not None and purpose in CAPS:
            used = _run_base.get(purpose, 0) + _calls[purpose] - _run_calls[purpose]
            if used >= CAPS[purpose]:
                raise BudgetSpent(purpose)
        _calls[purpose] += 1


def _release(purpose: Purpose) -> None:
    with _calls_lock:
        _calls[purpose] -= 1


def _sum_tokens(purpose: Purpose, stdout: str) -> None:
    sample = claude_usage.usage_sample(stdout)
    if sample is None:
        return
    with _calls_lock:
        for key in TOKEN_KEYS:
            value = sample.get(key)
            if isinstance(value, int):
                _tokens[purpose][key] += value


def argv(
    prompt: str,
    *,
    schema: dict[str, Any] | None = None,
) -> list[str]:
    """A `claude -p` command: no tools, no session kept, a JSON envelope."""
    command = [
        "claude",
        "-p",
        "--safe-mode",
        "--no-session-persistence",
        "--output-format",
        "json",
    ]
    if schema is not None:
        command += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    return [*command, prompt]


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = TIMEOUT_SECONDS,
    purpose: Purpose = "draft",
) -> str:
    """Run one call; its envelope, or `ModelError` when the model did not do the work."""
    # Budget is spent before anything spawns; a refused call never leaves here.
    _reserve(purpose)
    try:
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        # It ran and was spent, though it answered nothing.
        raise
    except OSError:
        # Never started: the call was not spent.
        _release(purpose)
        raise
    _sum_tokens(purpose, process.stdout)
    claude_usage.record(process.stdout)
    return checked_output(process.stdout, process.returncode)


def calls(*purposes: Purpose) -> int:
    """How many calls this process has made for these purposes; every purpose if none."""
    with _calls_lock:
        return sum(_calls[purpose] for purpose in purposes or PURPOSES)


def counts() -> dict[str, int]:
    """Every purpose's calls so far in this process."""
    with _calls_lock:
        return {purpose: _calls[purpose] for purpose in PURPOSES}


def structured(output: str) -> Any:
    """The schema's answer inside an envelope, else the envelope itself."""
    payload = json.loads(output)
    if isinstance(payload, dict) and "structured_output" in payload:
        return payload["structured_output"]
    return payload
