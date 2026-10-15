"""Resolution order: accepted patterns only, always through a fact, and the misses."""

from __future__ import annotations

from typing import Any

from personal_os_worker.jobs.resolve import Knowledge, Resolver, VerdictResolver
from personal_os_worker.jobs.short_answers import Verdict

YES_NO = {"tag": "select", "options": ["Yes", "No"]}


def knowledge(
    facts: dict[str, Any],
    patterns: list[dict[str, Any]] | None = None,
) -> Knowledge:
    return Knowledge.build(
        facts=[{"key": key, "value": value} for key, value in facts.items()],
        career=None,
        patterns=patterns or [],
        company="Acme",
    )


def ask(resolver: Resolver, question: str, field: dict[str, Any] | None = None) -> Any:
    return resolver.resolve((field or YES_NO) | {"label": question})


def binding(question_key: str, fact_key: str, status: str = "active") -> dict[str, Any]:
    return {"id": f"b-{fact_key}", "kind": "binding", "question_key": question_key,
            "fact_key": fact_key, "resolver_id": f"binding:{fact_key}", "status": status}


def test_an_active_binding_answers_before_anything_else() -> None:
    resolver = Resolver(knowledge(
        {"work_auth.requires_sponsorship": False, "preferences.willing_to_relocate": True},
        patterns=[binding("will you require sponsorship", "preferences.willing_to_relocate")],
    ))
    result = ask(resolver, "Will you require sponsorship?")
    # Odd binding, but it is what was accepted: the binding wins over expressions.
    assert (result.value, result.source, result.fact_key, result.pattern_id) == (
        "Yes", "pattern", "preferences.willing_to_relocate", "b-preferences.willing_to_relocate"
    )


def test_candidates_and_rejected_patterns_are_never_read() -> None:
    for status in ("candidate", "rejected", "retired"):
        resolver = Resolver(knowledge(
            {"work_auth.requires_sponsorship": False, "preferences.willing_to_relocate": True},
            patterns=[binding("do you need a visa", "preferences.willing_to_relocate", status)],
        ))
        assert ask(resolver, "Do you need a visa?") is None


def test_an_expression_answers_any_phrasing_when_exactly_one_fact_matches() -> None:
    expression = {"id": "e1", "kind": "expression", "expression": r"\bsponsor",
                  "fact_key": "work_auth.requires_sponsorship", "resolver_id": "expression:e1",
                  "status": "active"}
    resolver = Resolver(knowledge({"work_auth.requires_sponsorship": False},
                                  patterns=[expression]))
    result = ask(resolver, "Would Acme need to sponsor you?")
    assert (result.value, result.source, result.resolver_id) == ("No", "pattern", "expression:e1")
    clash = dict(expression, id="e2", fact_key="work_auth.us_citizen", resolver_id="e2")
    resolver = Resolver(knowledge({"work_auth.requires_sponsorship": False,
                                   "work_auth.us_citizen": True}, patterns=[expression, clash]))
    # Two expressions naming different facts: neither wins.
    assert ask(resolver, "Would Acme need to sponsor you?") is None


def test_without_a_pattern_nothing_answers_even_an_old_question() -> None:
    # The archive is for the facts session only: a question the old system
    # answered is still unanswered until a pattern is accepted for it.
    resolver = Resolver(knowledge({"work_auth.requires_sponsorship": False}))
    assert ask(resolver, "Will you now or in the future require visa sponsorship?") is None
    assert resolver.misses == {}


def test_misses_say_which_fact_is_missing_or_what_did_not_fit() -> None:
    resolver = Resolver(knowledge({"education.gpa": 3.78}, patterns=[
        binding("are you at least 18 years of age", "identity.adult"),
        binding("what is your gpa", "education.gpa"),
    ]))
    assert ask(resolver, "Are you at least 18 years of age?") is None
    adult = resolver.misses["are you at least 18 years of age"]
    assert (adult.stage, adult.fact_key) == ("pattern", "identity.adult")
    assert "no fact identity.adult" in adult.note

    assert ask(resolver, "What is your GPA?", {"tag": "select", "options": ["A", "B"]}) is None
    gpa = resolver.misses["what is your gpa"]
    assert (gpa.stage, gpa.fact_key) == ("format", "education.gpa")


def test_the_model_verdict_renders_its_fact_like_any_other() -> None:
    facts = knowledge({"education.school_name": "Example Institute of Technology"})
    resolver = VerdictResolver(
        {"Which campus?": Verdict("Which campus?", "fits_fact", fact_key="education.school_name")},
        facts,
    )
    result = resolver.resolve({"tag": "input", "label": "Which campus?"})
    assert (result.value, result.source, result.resolver_id, result.fact_key) == (
        "Example Institute of Technology", "model", "model:fact_v1", "education.school_name"
    )
    assert resolver.resolve({"tag": "input", "label": "Other?"}) is None
