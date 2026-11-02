"""Per-run counting of `claude -p` calls: no caps, just what each lane spent."""

import json
import subprocess

import pytest

from personal_os_worker.jobs import model
from personal_os_worker.jobs.autofill_timing import AutofillTimings

ENVELOPE = {
    "type": "result",
    "result": "ok",
    "usage": {
        "input_tokens": 600,
        "output_tokens": 40,
        "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 0,
    },
}


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every argv `model.run` would launch; none ever do."""
    spawned: list[list[str]] = []
    process = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=json.dumps(ENVELOPE)
    )
    monkeypatch.setattr(
        model.subprocess, "run",
        lambda argv, **_: spawned.append(list(argv)) or process,
    )
    return spawned


def test_every_call_counts_under_its_lane(spawned: list[list[str]]) -> None:
    model.begin_run()
    model.run(["claude", "-p"], purpose="pattern")
    model.run(["claude", "-p"], purpose="pattern")
    model.run(["claude", "-p"], purpose="audit")
    model.run(["claude", "-p"], purpose="draft")
    model.run(["claude", "-p"], purpose="code")

    counts = model.run_counts()
    assert counts == {"pattern": 2, "audit": 1, "draft": 1, "code": 1}
    assert len(spawned) == 5


def test_no_lane_is_capped(spawned: list[list[str]]) -> None:
    model.begin_run()
    for _ in range(200):
        model.run(["claude", "-p"], purpose="pattern")
    assert len(spawned) == 200
    assert model.run_counts()["pattern"] == 200


def test_a_reloaded_run_counts_the_calls_its_processes_made(
    spawned: list[list[str]],
) -> None:
    model.begin_run(base={"pattern": 19, "code": 4})
    model.run(["claude", "-p"], purpose="pattern")
    assert model.run_counts()["pattern"] == 20
    assert model.run_counts()["code"] == 4


def test_a_call_that_never_started_did_not_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*_: object, **__: object) -> None:
        raise OSError("claude: command not found")

    monkeypatch.setattr(model.subprocess, "run", missing)
    model.begin_run()
    with pytest.raises(OSError):
        model.run(["claude", "-p"], purpose="pattern")
    assert model.run_counts()["pattern"] == 0


def test_a_timed_out_call_still_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*_: object, **__: object) -> None:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(model.subprocess, "run", timeout)
    model.begin_run()
    with pytest.raises(subprocess.TimeoutExpired):
        model.run(["claude", "-p"], purpose="pattern")
    assert model.run_counts()["pattern"] == 1


def test_run_usage_sums_each_lanes_tokens(spawned: list[list[str]]) -> None:
    model.begin_run()
    model.run(["claude", "-p"], purpose="pattern")
    model.run(["claude", "-p"], purpose="pattern")
    model.run(["claude", "-p"], purpose="audit")

    usage = model.run_usage()
    assert usage["pattern"] == {
        "calls": 2, "input_tokens": 1200, "output_tokens": 80,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 200,
    }
    assert usage["audit"]["calls"] == 1
    assert usage["audit"]["input_tokens"] == 600
    assert usage["code"] == {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    }


def test_the_closing_line_counts_calls_by_lane() -> None:
    messages: list[str] = []
    timings = AutofillTimings(report=messages.append)
    usage = {
        purpose: {"calls": calls, "input_tokens": 0, "output_tokens": 0,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        for purpose, calls in (("pattern", 2), ("audit", 3), ("draft", 1), ("code", 1))
    }
    timings.close(usage)
    assert messages[0] == (
        "Model calls this run: pattern 2, audit 3, draft 1, code 1 (3 learning)"
    )
