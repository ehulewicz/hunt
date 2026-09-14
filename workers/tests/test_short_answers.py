import json
from typing import Any

from personal_os_worker.short_answers import ShortQuestion, draft_short_answers

RESUME = {"education": [{"org": "Example Institute of Technology"}]}
PROFILE: dict[str, Any] = {}
LIBRARY: list[dict[str, Any]] = []
POSTING = {"company": "Acme", "role": "Backend Engineer Intern", "description": "Build things."}


def scripted(payload: dict[str, Any]) -> Any:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        calls.append(argv)
        return json.dumps(payload)

    return runner, calls


def ask(
    questions: list[ShortQuestion], runner: Any, *, resume: dict[str, Any] = RESUME
) -> dict[str, str]:
    return draft_short_answers(
        questions,
        posting=POSTING,
        resume=resume,
        profile=PROFILE,
        library=LIBRARY,
        runner=runner,
    )


def test_high_confidence_choice_answer_rejected_when_not_verbatim_in_options() -> None:
    question = ShortQuestion("Consider me for other roles?", ("Yes", "No"))
    runner, _ = scripted(
        {"answers": [{"question": question.question, "answer": "yes", "confidence": "high"}]}
    )
    assert ask([question], runner) == {}


def test_high_confidence_choice_answer_present_verbatim_is_accepted() -> None:
    question = ShortQuestion("Consider me for other roles?", ("Yes", "No"))
    runner, _ = scripted(
        {"answers": [{"question": question.question, "answer": "Yes", "confidence": "high"}]}
    )
    assert ask([question], runner) == {question.question: "Yes"}


def test_free_text_answer_present_in_the_resume_is_accepted() -> None:
    question = ShortQuestion("What school are you attending?", ())
    runner, _ = scripted(
        {
            "answers": [
                {
                    "question": question.question,
                    "answer": "Example Institute of Technology",
                    "confidence": "high",
                }
            ]
        }
    )
    assert ask([question], runner) == {question.question: "Example Institute of Technology"}


def test_free_text_answer_with_an_unsupported_number_is_rejected() -> None:
    question = ShortQuestion("What is your expected salary?", ())
    runner, _ = scripted(
        {"answers": [{"question": question.question, "answer": "$120,000", "confidence": "high"}]}
    )
    assert ask([question], runner) == {}


def test_low_confidence_answer_is_dropped() -> None:
    question = ShortQuestion("What school are you attending?", ())
    runner, _ = scripted(
        {
            "answers": [
                {
                    "question": question.question,
                    "answer": "Example Institute of Technology",
                    "confidence": "low",
                }
            ]
        }
    )
    assert ask([question], runner) == {}


def test_non_json_output_answers_nothing() -> None:
    def runner(argv: list[str]) -> str:
        return "not json"

    question = ShortQuestion("What school are you attending?", ())
    assert ask([question], runner) == {}


def test_empty_question_list_never_calls_the_runner() -> None:
    def runner(argv: list[str]) -> str:
        raise AssertionError("runner should not be called for an empty batch")

    assert ask([], runner) == {}


def test_one_runner_call_covers_the_whole_batch() -> None:
    questions = [
        ShortQuestion("What school are you attending?", ()),
        ShortQuestion("Consider me for other roles?", ("Yes", "No")),
    ]
    runner, calls = scripted({"answers": []})
    ask(questions, runner)
    assert len(calls) == 1
    assert "What school are you attending?" in calls[0][-1]
    assert "Consider me for other roles?" in calls[0][-1]
