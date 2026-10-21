import json
from typing import Any

from personal_os_worker.jobs.short_answers import ShortQuestion, Verdict, draft_short_answers

POSTING = {"company": "Acme", "role": "Backend Engineer Intern"}
FACTS: list[dict[str, Any]] = [
    {"key": "education.school_name", "label": "School", "type": "text",
     "value": "Example Institute of Technology"},
    {"key": "work_auth.requires_sponsorship", "label": "Requires sponsorship", "type": "boolean",
     "value": False},
    {"key": "contact.phone", "label": "Phone number", "type": "text", "value": "(withheld)"},
]


def item(question: str, verdict: str, fact_key: str = "", missing: str = "") -> dict[str, str]:
    return {"question": question, "verdict": verdict, "fact_key": fact_key,
            "missing_fact": missing}


def ask(questions: list[ShortQuestion], runner: Any) -> dict[str, Verdict]:
    return draft_short_answers(questions, posting=POSTING, facts=FACTS, runner=runner)


def scripted(*items: dict[str, str]) -> Any:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        calls.append(argv)
        return json.dumps({"answers": list(items)})

    return runner, calls


def test_a_fits_fact_verdict_names_a_listed_fact() -> None:
    question = ShortQuestion("Which school do you attend?", ())
    runner, calls = scripted(item(question.question, "fits_fact", "education.school_name"))
    assert ask([question], runner) == {
        question.question: Verdict(question.question, "fits_fact", fact_key="education.school_name")
    }
    prompt = calls[0][-1]
    assert "education.school_name" in prompt and "(withheld)" in prompt
    assert "5550001111" not in prompt


def test_a_fits_fact_verdict_may_propose_an_expression_for_every_wording() -> None:
    question = ShortQuestion("Which school do you attend?", ())
    runner, calls = scripted(
        item(question.question, "fits_fact", "education.school_name")
        | {"expression": r"  \bwhich (school|university)\b "}
    )
    assert ask([question], runner)[question.question].expression == (
        r"\bwhich (school|university)\b"
    )
    # The model is told it names a fact and an expression, never a value.
    schema = json.loads(calls[0][calls[0].index("--json-schema") + 1])
    answer = schema["properties"]["answers"]["items"]["properties"]
    assert set(answer) == {"question", "verdict", "fact_key", "expression", "missing_fact"}


def test_a_fact_key_that_was_not_listed_is_only_unsure() -> None:
    question = ShortQuestion("Do you hold a clearance?", ("Yes", "No"))
    runner, _ = scripted(item(question.question, "fits_fact", "security.clearance"))
    assert ask([question], runner)[question.question].kind == "unsure"


def test_no_fact_names_the_missing_fact() -> None:
    question = ShortQuestion("Do you hold an active security clearance?", ("Yes", "No"))
    runner, _ = scripted(
        item(question.question, "no_fact", missing="  security   clearance status ")
    )
    verdict = ask([question], runner)[question.question]
    assert verdict == Verdict(
        question.question, "no_fact", missing_fact="security clearance status"
    )
    unnamed, _ = scripted(item(question.question, "no_fact"))
    assert ask([question], unnamed)[question.question].kind == "unsure"


def test_answers_to_questions_never_asked_are_ignored() -> None:
    question = ShortQuestion("Which school?", ())
    runner, _ = scripted(item("Something else?", "fits_fact", "education.school_name"))
    assert ask([question], runner) == {}


def test_non_json_output_answers_nothing() -> None:
    assert ask([ShortQuestion("Which school?", ())], lambda argv: "not json") == {}


def test_empty_question_list_never_calls_the_runner() -> None:
    def runner(argv: list[str]) -> str:
        raise AssertionError("runner should not be called for an empty batch")

    assert ask([], runner) == {}


def test_each_question_is_its_own_call_and_the_verdicts_merge() -> None:
    import threading
    import time

    questions = [
        ShortQuestion("What school are you attending?", ()),
        ShortQuestion("Will you require sponsorship?", ("Yes", "No")),
    ]
    started: list[float] = []
    lock = threading.Lock()

    def runner(argv: list[str]) -> str:
        with lock:
            started.append(time.monotonic())
        time.sleep(0.2)
        if "Will you require sponsorship?" in argv[-1]:
            answer = item(questions[1].question, "fits_fact", "work_auth.requires_sponsorship")
        else:
            answer = item(questions[0].question, "fits_fact", "education.school_name")
        return json.dumps({"answers": [answer]})

    begun = time.monotonic()
    verdicts = ask(questions, runner)
    elapsed = time.monotonic() - begun

    assert {question: verdict.fact_key for question, verdict in verdicts.items()} == {
        questions[0].question: "education.school_name",
        questions[1].question: "work_auth.requires_sponsorship",
    }
    assert len(started) == 2
    assert elapsed < 0.35  # the two 0.2s calls overlapped
