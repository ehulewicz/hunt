"""The audit's classification and its budget."""

from __future__ import annotations

from typing import Any

from personal_os_worker.jobs.answer_check import (
    FilledAnswer,
    Judgement,
    check_answers,
    judgements_from,
)
from personal_os_worker.jobs.audit import Budget, Finding, classify_empty, order, summarize
from personal_os_worker.jobs.resolve import Miss


def classify(question: str, **kwargs: Any) -> Finding:
    return classify_empty(
        question,
        required=kwargs.pop("required", True),
        needed=kwargs.pop("needed", {}),
        misses=kwargs.pop("misses", {}),
        failures=kwargs.pop("failures", {}),
    )


def test_a_recorded_strategy_failure_is_a_control_error() -> None:
    finding = classify(
        "Phone",
        failures={
            "phone": {
                "stopped_at": "strategy",
                "error_message": "value did not register",
                "intended_value": "555",
            }
        },
    )
    assert (finding.cause, finding.intended_value) == ("control_error", "555")


def test_a_recorded_format_failure_keeps_the_fact_and_formatter() -> None:
    finding = classify(
        "Graduation term",
        failures={
            "graduation term": {
                "stopped_at": "format",
                "fact_key": "education.graduation_date",
                "formatter": "date_as_term",
                "error_message": "no unambiguous select option",
            }
        },
    )
    assert finding.cause == "format_error"
    assert finding.fact_key == "education.graduation_date"
    assert finding.formatter == "date_as_term"


def test_a_fact_the_model_named_is_a_fact_gap() -> None:
    finding = classify("Clearance?", needed={"Clearance?": "security clearance status"})
    assert (finding.cause, finding.missing_fact) == ("fact_gap", "security clearance status")
    assert "security clearance status" in finding.note


def test_a_pattern_miss_naming_a_missing_fact_is_a_fact_gap() -> None:
    miss = Miss("Veteran status", "pattern", "eeo.veteran_status", "pattern:1",
                note="no fact eeo.veteran_status yet")
    finding = classify("Veteran status", misses={"veteran status": miss})
    assert (finding.cause, finding.fact_key) == ("fact_gap", "eeo.veteran_status")


def test_a_format_miss_is_a_format_error() -> None:
    miss = Miss("Start date", "format", "availability.earliest_start_date", "pattern:2",
                "date_as_iso", "fits none of the options")
    finding = classify("Start date", misses={"start date": miss})
    assert (finding.cause, finding.formatter) == ("format_error", "date_as_iso")


def test_nothing_known_about_a_question_is_a_pattern_gap() -> None:
    finding = classify("How many languages do you speak?", required=False)
    assert finding.cause == "pattern_gap"
    assert finding.required is False


def test_a_failure_that_stopped_nowhere_falls_through_to_the_resolver() -> None:
    finding = classify("Email", failures={"email": {"stopped_at": None}})
    assert finding.cause == "pattern_gap"


def test_required_questions_and_actionable_causes_come_first() -> None:
    findings = [
        Finding("optional control", "control_error", False, ""),
        Finding("needs a fact", "fact_gap", True, ""),
        Finding("bad widget", "control_error", True, ""),
    ]
    assert [item.question for item in order(findings)] == [
        "bad widget",
        "needs a fact",
        "optional control",
    ]


def test_the_summary_counts_each_cause() -> None:
    findings = [
        Finding("a", "fact_gap", True, ""),
        Finding("b", "fact_gap", True, ""),
        Finding("c", "pattern_gap", False, ""),
    ]
    assert summarize(findings) == "2 fact gap, 1 pattern gap"


def test_the_budget_caps_one_question_then_the_whole_run() -> None:
    budget = Budget(per_question=2, per_run=3)
    assert budget.allows("A")
    budget.charge("A", 2)
    assert not budget.allows("A")
    assert "2 repair operations" in str(budget.exhausted("A"))
    assert budget.allows("B")
    budget.charge("B")
    assert budget.exhausted("B") is not None
    assert "budget of 3 operations" in str(budget.exhausted("B"))


# ----- the wrongness pass ------------------------------------------------


def test_a_wrong_judgement_must_name_a_listed_fact() -> None:
    answers = [FilledAnswer("Sponsorship?", "Yes", ("Yes", "No"), "pattern")]
    judged = judgements_from(
        [
            {"question": "Sponsorship?", "verdict": "wrong",
             "fact_key": "work_auth.requires_sponsorship", "note": "the fact says no"},
        ],
        answers,
        {"work_auth.requires_sponsorship"},
    )
    assert judged["Sponsorship?"] == Judgement(
        "Sponsorship?", "wrong", "work_auth.requires_sponsorship", "the fact says no"
    )


def test_a_wrong_judgement_naming_an_unlisted_fact_is_unknown() -> None:
    answers = [FilledAnswer("Sponsorship?", "Yes")]
    judged = judgements_from(
        [{"question": "Sponsorship?", "verdict": "wrong", "fact_key": "invented.key",
          "note": ""}],
        answers,
        {"work_auth.requires_sponsorship"},
    )
    assert judged["Sponsorship?"].kind == "unknown"


def test_a_judgement_for_a_question_never_asked_is_dropped() -> None:
    judged = judgements_from(
        [{"question": "Something else", "verdict": "wrong", "fact_key": "a.b", "note": ""}],
        [FilledAnswer("Sponsorship?", "Yes")],
        {"a.b"},
    )
    assert judged == {}


def test_check_answers_sends_one_call_per_answer_and_reads_every_verdict() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        calls.append(argv)
        question = "Sponsorship?" if "Sponsorship?" in argv[-1] else "Citizen?"
        verdict = "wrong" if question == "Sponsorship?" else "right"
        return (
            '{"structured_output": {"judgements": [{"question": "' + question + '",'
            '"verdict": "' + verdict + '", "fact_key": '
            + ('"work_auth.requires_sponsorship"' if verdict == "wrong" else '""')
            + ', "note": "n"}]}}'
        )

    judged = check_answers(
        [FilledAnswer("Sponsorship?", "Yes"), FilledAnswer("Citizen?", "Yes")],
        posting={"company": "Acme", "role": "Intern"},
        facts=[{"key": "work_auth.requires_sponsorship", "label": "Sponsorship", "value": False}],
        runner=runner,
    )
    assert len(calls) == 2
    assert judged["Sponsorship?"].kind == "wrong"
    assert judged["Citizen?"].kind == "right"
    # The value is in the prompt: the judgement is made against the fact, not a label.
    assert "requires_sponsorship" in calls[0][-1]


def test_check_answers_without_answers_makes_no_call() -> None:
    def runner(argv: list[str]) -> str:  # pragma: no cover - must not run
        raise AssertionError("no call expected")

    assert check_answers([], posting={}, facts=[], runner=runner) == {}


def test_a_worker_filled_answer_is_distinguished_from_yours() -> None:
    assert FilledAnswer("q", "a", (), "pattern").worker_filled is True
    assert FilledAnswer("q", "a", (), "model").worker_filled is True
    assert FilledAnswer("q", "a", (), "manual").worker_filled is False
    assert FilledAnswer("q", "a", (), "prose").worker_filled is False
