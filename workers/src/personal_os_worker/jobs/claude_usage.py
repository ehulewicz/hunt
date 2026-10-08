"""Content-free token and cost accounting for the worker's `claude -p` calls.

Every call site already runs `--output-format json` and reads only
`structured_output`. The same envelope carries usage and cost, so per-
application accounting costs nothing extra to collect.

Only numbers and the model identifier are kept. Nothing here ever reads the
prompt or the answer, which is what keeps `AutofillTimings` safe to print.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

Observer = Callable[[dict[str, Any]], None]

# A plain module global rather than a ContextVar: the short-answer pass runs its
# calls in a ThreadPoolExecutor, which does not carry context into its workers.
_observer: Observer | None = None
_lock = threading.Lock()


@contextmanager
def observing(observer: Observer) -> Iterator[None]:
    """Route usage from any `claude -p` call made in this block to `observer`."""
    global _observer
    previous = _observer
    _observer = observer
    try:
        yield
    finally:
        _observer = previous


def record(output: str) -> None:
    """Report one `claude -p --output-format json` envelope's usage, if anyone asked."""
    observer = _observer
    if observer is None:
        return
    sample = usage_sample(output)
    if sample is None:
        return
    with _lock:
        observer(sample)


def usage_sample(output: str) -> dict[str, Any] | None:
    """The usage numbers in a CLI envelope, or None when it carries none."""
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    usage = _mapping(payload.get("usage"))
    details = _mapping(usage.get("output_tokens_details"))
    models = _mapping(payload.get("modelUsage"))
    sample = {
        "model": ",".join(sorted(str(name) for name in models)) or None,
        "input_tokens": _whole(usage.get("input_tokens")),
        "output_tokens": _whole(usage.get("output_tokens")),
        "thinking_tokens": _whole(details.get("thinking_tokens")),
        "cache_read_input_tokens": _whole(usage.get("cache_read_input_tokens")),
        "cache_creation_input_tokens": _whole(usage.get("cache_creation_input_tokens")),
        "cost_usd": _amount(payload.get("total_cost_usd")),
        "duration_api_ms": _whole(payload.get("duration_api_ms")),
        "num_turns": _whole(payload.get("num_turns")),
    }
    if all(value is None for key, value in sample.items() if key != "model"):
        return None
    return sample


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _whole(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _amount(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), 6)
