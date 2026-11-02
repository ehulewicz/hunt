"""Content-free phase timings for the Jobs application loop."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any

from personal_os_worker.jobs import claude_usage


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
            # Any `claude -p` call made inside the phase is attributed to it, so
            # a call site never has to know which phase it is serving.
            with claude_usage.observing(lambda sample: self.record_usage(phase, step, sample)):
                yield
            outcome = "ok"
        finally:
            elapsed = round((self.clock() - started) * 1000)
            self.samples.append({
                "phase": phase, "step": step, "elapsed_ms": elapsed, "outcome": outcome,
            })
            self.report(f"Timing step {step + 1}: {phase} {elapsed} ms ({outcome})")

    def close(self, usage: dict[str, dict[str, int]]) -> None:
        """The run's closing lines: its model calls per lane, then its tokens.
        Learning — pattern and code — is what the next application on this
        site should not need."""
        by_purpose = ", ".join(
            f"{purpose} {sums['calls']}" for purpose, sums in usage.items()
        )
        learning = (
            usage.get("pattern", {}).get("calls", 0)
            + usage.get("code", {}).get("calls", 0)
        )
        self.report(f"Model calls this run: {by_purpose} ({learning} learning)")

        def tokens(purpose: str) -> str:
            sums = usage.get(purpose, {})
            line = (
                f"{purpose} {sums.get('input_tokens', 0)} in / "
                f"{sums.get('output_tokens', 0)} out"
            )
            cached = sums.get("cache_read_input_tokens", 0)
            if cached:
                line += f" ({cached} cached)"
            return line

        self.report("Model tokens this run: " + ", ".join(map(tokens, usage)))

    def record_usage(self, phase: str, step: int, sample: dict[str, Any]) -> None:
        """Keep one Claude call's tokens and reported cost beside the phase timings."""
        self.samples.append({"kind": "claude_usage", "phase": phase, "step": step, **sample})
        tokens = f"{sample.get('input_tokens')} in / {sample.get('output_tokens')} out"
        cached = sample.get("cache_read_input_tokens")
        if cached:
            tokens += f" ({cached} cached)"
        cost = sample.get("cost_usd")
        priced = f", ${cost:.4f}" if isinstance(cost, float) else ""
        self.report(f"Claude step {step + 1}: {phase} {tokens}{priced}")
