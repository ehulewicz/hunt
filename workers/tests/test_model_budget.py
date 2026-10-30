"""Per-run caps on `claude -p` calls, and what a spent cap leaves running."""

import json
import subprocess
from types import SimpleNamespace
from typing import Any, cast

import pytest

from personal_os_worker.jobs import model
from personal_os_worker.jobs.answer_check import FilledAnswer, Judgement
from personal_os_worker.jobs.autofill_session import AutofillSession
from personal_os_worker.jobs.autofill_timing import AutofillTimings
from personal_os_worker.jobs.model_errors import ModelError
from personal_os_worker.jobs.short_answers import ShortQuestion, draft_short_answers

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


def new_session() -> AutofillSession:
    return AutofillSession(cast(Any, SimpleNamespace(
        api=None, run_id="run-budget",
        page=SimpleNamespace(url="https://example.test"),
    )))


def test_a_purpose_that_hits_its_cap_never_spawns_again(spawned: list[list[str]]) -> None:
    model.begin_run()
    for _ in range(model.CAPS["learn"]):
        model.run(["claude", "-p"], purpose="learn")

    with pytest.raises(model.BudgetSpent, match="20 learning calls"):
        model.run(["claude", "-p"], purpose="learn")

    assert len(spawned) == 20
    # Only learning is spent: checking, drafting, and the uncapped fixer still run.
    model.run(["claude", "-p"], purpose="check")
    model.run(["claude", "-p"], purpose="draft")
    for _ in range(25):
        model.run(["claude", "-p"], purpose="fix")
    assert len(spawned) == 47


def test_a_reloaded_run_counts_the_calls_its_processes_made(
    spawned: list[list[str]],
) -> None:
    model.begin_run(base={"learn": model.CAPS["learn"] - 1})
    model.run(["claude", "-p"], purpose="learn")
    with pytest.raises(model.BudgetSpent):
        model.run(["claude", "-p"], purpose="learn")
    assert len(spawned) == 1


def test_outside_a_run_nothing_is_capped(spawned: list[list[str]]) -> None:
    model.begin_run()
    model.end_run()
    for _ in range(model.CAPS["learn"] + 1):
        model.run(["claude", "-p"], purpose="learn")
    assert len(spawned) == model.CAPS["learn"] + 1
    assert model.remaining("learn") is None


def test_a_call_that_never_started_did_not_spend_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*_: object, **__: object) -> None:
        raise OSError("claude: command not found")

    monkeypatch.setattr(model.subprocess, "run", missing)
    model.begin_run()
    with pytest.raises(OSError):
        model.run(["claude", "-p"], purpose="learn")
    assert model.remaining("learn") == model.CAPS["learn"]


def test_run_usage_sums_each_purposes_tokens(spawned: list[list[str]]) -> None:
    model.begin_run()
    model.run(["claude", "-p"], purpose="learn")
    model.run(["claude", "-p"], purpose="learn")
    model.run(["claude", "-p"], purpose="check")

    usage = model.run_usage()
    assert usage["learn"] == {
        "calls": 2, "input_tokens": 1200, "output_tokens": 80,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 200,
    }
    assert usage["check"]["calls"] == 1
    assert usage["check"]["input_tokens"] == 600
    assert usage["fix"] == {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    }


def _question_of(argv: list[str]) -> str:
    line = next(line for line in argv[-1].splitlines() if line.startswith("QUESTION:"))
    return json.loads(line.removeprefix("QUESTION:").strip())["question"]


def _verdict(question: str) -> str:
    return json.dumps({"structured_output": {"answers": [{
        "question": question, "verdict": "no_fact", "fact_key": "",
        "expression": "", "missing_fact": "security clearance status",
    }]}})


def test_a_batch_mid_cap_keeps_the_verdicts_already_paid_for() -> None:
    left = 2

    def runner(argv: list[str]) -> str:
        nonlocal left
        if left == 0:
            raise model.BudgetSpent("learn")
        left -= 1
        return _verdict(_question_of(argv))

    questions = [ShortQuestion(f"Question {index}?", ()) for index in range(3)]
    verdicts = draft_short_answers(questions, posting={}, facts=[], runner=runner)

    # The pool races for the two calls left; exactly two verdicts are kept.
    assert len(verdicts) == 2
    assert set(verdicts) <= {question.question for question in questions}
    assert all(verdict.kind == "no_fact" for verdict in verdicts.values())


def test_a_batch_with_no_calls_left_reraises_the_spent_cap() -> None:
    def runner(argv: list[str]) -> str:
        raise model.BudgetSpent("learn")

    questions = [ShortQuestion(f"Question {index}?", ()) for index in range(3)]
    with pytest.raises(model.BudgetSpent):
        draft_short_answers(questions, posting={}, facts=[], runner=runner)


def test_a_spent_cap_is_said_once_and_stops_only_its_purpose(
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = new_session()
    model.begin_run(base={"learn": model.CAPS["learn"]})

    assert session.call_model(model.run, ["claude", "-p"], purpose="learn") is None
    assert session.call_model(model.run, ["claude", "-p"], purpose="learn") is None
    assert session.state.model_error is None

    printed = capsys.readouterr().out
    assert printed.count("this run has made its 20 learning calls") == 1

    # The answer checker still runs: its cap is untouched.
    checked: list[list[FilledAnswer]] = []

    def checker(answers: list[FilledAnswer], **_: Any) -> dict[str, Judgement]:
        checked.append(answers)
        return {answer.question: Judgement(answer.question, "right") for answer in answers}

    answers = [FilledAnswer("Sponsorship?", "No")]
    assert session.call_model(
        checker, answers, posting={}, facts=[]
    ) == {"Sponsorship?": Judgement("Sponsorship?", "right")}
    assert checked == [answers]


def test_a_usage_limit_still_stops_every_call(capsys: pytest.CaptureFixture[str]) -> None:
    session = new_session()
    called: list[str] = []

    def limited(*_: Any, **__: Any) -> str:
        raise ModelError("Claude usage limit reached")

    assert session.call_model(limited) is None
    assert session.call_model(lambda: called.append("later")) is None
    assert "usage limit reached" in str(session.state.model_error)
    assert called == []
    assert "usage limit reached" in " ".join(capsys.readouterr().out.split())


def test_the_closing_lines_count_calls_against_the_caps() -> None:
    messages: list[str] = []
    timings = AutofillTimings(report=messages.append)
    usage = {
        "learn": {"calls": 2, "input_tokens": 1200, "output_tokens": 80,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "check": {"calls": 3, "input_tokens": 900, "output_tokens": 60,
                  "cache_read_input_tokens": 400, "cache_creation_input_tokens": 0},
        "draft": {"calls": 1, "input_tokens": 0, "output_tokens": 0,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "fix": {"calls": 1, "input_tokens": 0, "output_tokens": 0,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    }
    timings.close(usage, model.CAPS)
    assert messages == [
        "Model calls this run: learn 2 of 20, check 3 of 30, draft 1 of 10, "
        "fix 1 (3 learning and fixing)",
        "Model tokens this run: learn 1200 in / 80 out, check 900 in / 60 out "
        "(400 cached), draft 0 in / 0 out, fix 0 in / 0 out",
    ]
