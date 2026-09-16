"""Content-free phase timings for the Jobs application loop."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any


class AutofillTimings:
    def __init__(
        self, *, report: Callable[[str], None], clock: Callable[[], float] = perf_counter,
    ) -> None:
        self.clock = clock
        self.report = report
        self.samples: list[dict[str, Any]] = []

    @contextmanager
    def measure(self, phase: str, *, step: int) -> Iterator[None]:
        started = self.clock()
        outcome = "interrupted"
        try:
            yield
            outcome = "ok"
        finally:
            elapsed = round((self.clock() - started) * 1000)
            self.samples.append({
                "phase": phase, "step": step, "elapsed_ms": elapsed, "outcome": outcome,
            })
            self.report(f"Timing step {step + 1}: {phase} {elapsed} ms ({outcome})")
