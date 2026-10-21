"""Learning: what a model verdict may teach, checked against every question met (R67)."""

from __future__ import annotations

from typing import Any

from personal_os_worker.jobs.learn import (
    binding_refusal,
    expression_refusal,
    lesson_for,
    override_refusal,
)

PHONE = {"id": "e1", "kind": "expression", "status": "active", "origin": "inbox",
         "expression": r"^\s*(mobile\s+)?phone(\s+number)?\s*$", "fact_key": "contact.phone"}
EXTENSION = {"id": "e2", "kind": "expression", "status": "active", "origin": "inbox",
             "expression": r"\bextension\b", "fact_key": "contact.phone_extension"}
MET = ["Phone", "Phone Extension", "Alternate Phone Number", "Middle Name", "Email"]


def binding(question_key: str, fact_key: str, origin: str) -> dict[str, Any]:
    return {"id": f"b-{origin}", "kind": "binding", "status": "active", "origin": origin,
            "question_key": question_key, "fact_key": fact_key}


def test_an_expression_is_kept_when_it_changes_no_other_answer() -> None:
    expression = r"^\s*(alternate|other)\s+phone(\s+number)?\s*$"
    lesson, why = lesson_for(
        "Alternate Phone Number", "contact.phone", expression, patterns=[PHONE], questions=MET
    )
    assert lesson is not None and (lesson.kind, lesson.expression) == ("expression", expression)
    assert why == ""


def test_an_expression_that_would_change_another_answer_falls_back_to_a_binding() -> None:
    # "\bphone\b" also matches Phone Extension, which an expression answers already.
    lesson, _ = lesson_for(
        "Alternate Phone Number", "contact.phone", r"\bphone\b",
        patterns=[PHONE, EXTENSION], questions=MET,
    )
    assert lesson is not None and lesson.kind == "binding"
    assert "would change what 'Phone Extension' is answered from" in lesson.note


def test_an_expression_must_also_leave_an_unanswered_question_unanswered() -> None:
    refusal = expression_refusal(
        r"\bname\b", "Middle Name", "identity.middle_name",
        patterns=[], questions=["Middle Name", "Preferred Name"],
    )
    assert refusal == "it would change what 'Preferred Name' is answered from"


def test_an_expression_must_compile_match_and_decide_the_question() -> None:
    def refusal(expression: str, patterns: list[dict[str, Any]] | None = None) -> str | None:
        return expression_refusal(
            expression, "Phone", "contact.phone_country", patterns=patterns or [], questions=MET
        )

    assert refusal("(unbalanced").startswith("it does not compile")
    assert refusal(r"\bemail\b") == "it does not match the question"
    # Another fact already matches Phone, so a second leaves it undecided.
    assert refusal(r"^phone$", [PHONE]) == (
        "the active patterns would still not decide the question as that fact"
    )
    assert refusal("x" * 301) == "it runs past 300 characters"


def test_a_model_never_replaces_a_binding() -> None:
    """A binding's origin says who proposed it, not who accepted it."""
    for origin in ("inbox", "model"):
        bound = [binding("phone", "contact.phone", origin)]
        assert binding_refusal("Phone", patterns=bound) == (
            "the question is bound already, and a model never replaces a binding"
        )
        lesson, why = lesson_for(
            "Phone", "contact.phone_country", None, patterns=bound, questions=MET
        )
        assert lesson is None and "never replaces a binding" in why


def test_a_wrong_answer_overrides_only_a_pattern_a_model_taught() -> None:
    yours = [PHONE]
    assert override_refusal("Phone", patterns=yours) == (
        "a pattern you made answers it, and a model never overrides yours"
    )
    lesson, _ = lesson_for(
        "Phone", "contact.phone_country", None, patterns=yours, questions=MET, overriding=True
    )
    assert lesson is None
    taught = [PHONE | {"origin": "model"}]
    lesson, _ = lesson_for(
        "Phone", "contact.phone_country", None, patterns=taught, questions=MET, overriding=True
    )
    assert lesson is not None and lesson.kind == "binding"


def test_an_expression_may_not_nest_one_repetition_in_another() -> None:
    assert expression_refusal(
        r"^(\w+\s?)+$", "Phone", "contact.phone", patterns=[], questions=[]
    ) == "it nests one repetition inside another"
