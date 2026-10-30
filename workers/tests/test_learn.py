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
    # "^.*\bphone\b.*$" also matches Phone Extension, which an expression answers already.
    lesson, _ = lesson_for(
        "Alternate Phone Number", "contact.phone", r"^.*\bphone\b.*$",
        patterns=[PHONE, EXTENSION], questions=MET,
    )
    assert lesson is not None and lesson.kind == "binding"
    assert "it matches 'Phone Extension', which is answered from contact.phone_extension" in (
        lesson.note
    )


def test_a_branch_may_reach_a_recorded_question_nothing_answers_yet() -> None:
    # "Preferred Name" is recorded but no pattern answers it: replay guards it.
    refusal = expression_refusal(
        r"^.*\bname\b.*$", "Middle Name", "identity.middle_name",
        patterns=[], questions=["Middle Name", "Preferred Name"],
    )
    assert refusal is None


def test_a_short_label_needs_an_anchored_branch() -> None:
    assert expression_refusal(
        r"\bgender\b", "Gender", "eeo.gender_identity", patterns=[], questions=[]
    ) == "a label of four words or fewer needs an anchored expression"
    assert expression_refusal(
        r"^gender$", "Gender", "eeo.gender_identity", patterns=[], questions=[]
    ) is None


def test_a_label_form_counts_when_the_branch_matches() -> None:
    # 'Gender *' and 'Gender (optional):' are the question "Gender".
    assert expression_refusal(
        r"^gender$", "Gender *", "eeo.gender_identity", patterns=[], questions=[]
    ) is None


def test_a_branch_matching_a_bound_question_of_another_fact_is_refused() -> None:
    # A binding counts even where it would hide the branch (rule g).
    bound = binding("any security screening", "work_auth.security_clearance", "inbox")
    refusal = expression_refusal(
        r"^.*\bsecurity\b.*$", "Do you hold a security clearance?",
        "work_auth.sanctioned_country_citizen",
        patterns=[bound],
        questions=["Do you hold a security clearance?", "Any security screening?"],
    )
    assert refusal == (
        "it matches 'Any security screening?', which is answered "
        "from work_auth.security_clearance"
    )


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

    bound_model = [binding("phone", "contact.phone", "model")]
    lesson, _ = lesson_for(
        "Phone", "contact.phone_country", None, patterns=bound_model, questions=MET,
        overriding=True,
    )
    assert lesson is not None and lesson.kind == "binding"
    assert lesson.replaces == "b-model"
    assert lesson.shape()["replaces"] == "b-model"


def test_an_expression_may_not_nest_one_repetition_in_another() -> None:
    assert expression_refusal(
        r"^(\w+\s?)+$", "Phone", "contact.phone", patterns=[], questions=[]
    ) == "it nests one repetition inside another"


def test_expressions_match_the_label_and_its_stripped_form() -> None:
    import re

    from personal_os_worker.jobs.options import (
        expression_matches,
        label_forms,
        stripped_label,
    )

    assert stripped_label("Gender *") == "Gender"
    assert stripped_label("Gender (optional):") == "Gender"
    assert label_forms("Name") == ("Name",)
    regex = re.compile(r"^gender$", re.IGNORECASE)
    assert expression_matches(regex, "Gender *")
    assert expression_matches(regex, "Gender (optional):")
    assert expression_matches(
        re.compile(r"\bgender identity\b"),
        "How do you identify? (gender identity)",
    )
    assert expression_matches(
        re.compile(r"^(?!.*\brelocat\w*\b)(?=.*\bable\b)(?=.*\boffice\b)", re.IGNORECASE),
        "This role is based onsite in Somerville, MA and may require up to "
        "5 days/week in the office. Are you able to meet these requirements? "
        "(Please note that relocation assistance is not available for this position.)",
    )
