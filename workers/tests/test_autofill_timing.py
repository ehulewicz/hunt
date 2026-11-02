import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from personal_os_worker.jobs import claude_usage
from personal_os_worker.jobs.autofill_timing import AutofillTimings

ENVELOPE = {
    "type": "result",
    "result": "a private drafted answer",
    "total_cost_usd": 0.0064908,
    "duration_api_ms": 962,
    "num_turns": 1,
    "usage": {
        "input_tokens": 2,
        "output_tokens": 4,
        "cache_creation_input_tokens": 764,
        "cache_read_input_tokens": 1454,
        "output_tokens_details": {"thinking_tokens": 11},
    },
    "modelUsage": {"claude-opus-5-5[1m]": {"costUSD": 0.0064908}},
}


def test_timing_records_success_and_failure_without_answer_content() -> None:
    ticks = iter([1.0, 1.125, 2.0, 2.5])
    messages: list[str] = []
    timings = AutofillTimings(clock=lambda: next(ticks), report=messages.append)
    with timings.measure("deterministic", step=0):
        pass
    with (
        pytest.raises(RuntimeError, match="private answer"),
        timings.measure("long_form", step=1),
    ):
        raise RuntimeError("private answer")
    assert timings.samples == [
        {"phase": "deterministic", "step": 0, "elapsed_ms": 125, "outcome": "ok"},
        {"phase": "long_form", "step": 1, "elapsed_ms": 500, "outcome": "interrupted"},
    ]
    assert all("private answer" not in message for message in messages)


def test_timing_distinguishes_user_wait_from_automation() -> None:
    ticks = iter([0.0, 20.0, 20.0, 20.2])
    timings = AutofillTimings(clock=lambda: next(ticks), report=lambda _: None)
    with timings.measure("user_review", step=0):
        pass
    with timings.measure("deterministic", step=0):
        pass
    assert timings.samples[0]["elapsed_ms"] == 20000
    assert timings.samples[1]["elapsed_ms"] == 200


def test_claude_usage_is_kept_per_phase_without_any_answer_content() -> None:
    ticks = iter([0.0, 1.0])
    messages: list[str] = []
    timings = AutofillTimings(clock=lambda: next(ticks), report=messages.append)
    with timings.measure("short_drafts", step=2):
        claude_usage.record(json.dumps(ENVELOPE))
    usage = [sample for sample in timings.samples if sample.get("kind") == "claude_usage"]
    assert usage == [{
        "kind": "claude_usage",
        "phase": "short_drafts",
        "step": 2,
        "model": "claude-opus-5-5[1m]",
        "input_tokens": 2,
        "output_tokens": 4,
        "thinking_tokens": 11,
        "cache_read_input_tokens": 1454,
        "cache_creation_input_tokens": 764,
        "cost_usd": 0.006491,
        "duration_api_ms": 962,
        "num_turns": 1,
    }]
    assert all("private drafted answer" not in message for message in messages)
    assert any("$0.0065" in message for message in messages)


def test_claude_usage_from_pool_threads_lands_in_the_enclosing_phase() -> None:
    """The short-answer pass fans out through a thread pool; usage must survive it."""
    ticks = iter([0.0, 1.0])
    timings = AutofillTimings(clock=lambda: next(ticks), report=lambda _: None)
    with timings.measure("short_drafts", step=0), ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: claude_usage.record(json.dumps(ENVELOPE)), range(3)))
    usage = [sample for sample in timings.samples if sample.get("kind") == "claude_usage"]
    assert len(usage) == 3
    assert {sample["phase"] for sample in usage} == {"short_drafts"}


def test_claude_usage_outside_a_measured_phase_is_dropped() -> None:
    claude_usage.record(json.dumps(ENVELOPE))  # no observer installed; must not raise


def test_claude_usage_ignores_output_that_carries_no_usage() -> None:
    assert claude_usage.usage_sample("not json") is None
    assert claude_usage.usage_sample(json.dumps({"result": "ok"})) is None
    assert claude_usage.usage_sample(json.dumps(["a"])) is None


def test_each_model_call_is_counted_under_its_purpose(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from personal_os_worker.jobs import answer_check, cover_letter, model, short_answers

    process = subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(ENVELOPE))
    monkeypatch.setattr(model.subprocess, "run", lambda *_, **__: process)
    before = model.counts()
    short_answers.run_claude(["claude", "-p"])
    short_answers.run_claude(["claude", "-p"])
    answer_check.run_claude(["claude", "-p"])
    cover_letter.run_claude(["claude", "-p"])
    model.run(["claude", "-p"], purpose="code")
    after = model.counts()

    assert {purpose: after[purpose] - before[purpose] for purpose in model.PURPOSES} == {
        "pattern": 2, "audit": 1, "draft": 1, "code": 1,
    }
    assert model.calls("pattern", "code") - (before["pattern"] + before["code"]) == 3


def test_a_timed_out_call_still_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from personal_os_worker.jobs import model

    def timeout(*_: object, **__: object) -> None:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(model.subprocess, "run", timeout)
    before = model.calls("pattern")
    with pytest.raises(subprocess.TimeoutExpired):
        model.run(["claude", "-p"], purpose="pattern")
    assert model.calls("pattern") == before + 1


def test_the_closing_line_counts_calls_by_purpose() -> None:

    messages: list[str] = []
    timings = AutofillTimings(report=messages.append)
    usage = {
        purpose: {"calls": calls, "input_tokens": 0, "output_tokens": 0,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        for purpose, calls in (("pattern", 2), ("audit", 3), ("draft", 1), ("code", 1))
    }
    timings.close(usage)
    assert messages == [
        "Model calls this run: pattern 2, audit 3, draft 1, code 1 (3 learning)",
        "Model tokens this run: pattern 0 in / 0 out, audit 0 in / 0 out, "
        "draft 0 in / 0 out, code 0 in / 0 out",
    ]
