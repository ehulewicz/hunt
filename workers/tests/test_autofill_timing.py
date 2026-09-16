import pytest

from personal_os_worker.autofill_timing import AutofillTimings


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
