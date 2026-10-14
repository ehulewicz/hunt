"""Resolution order, the archive's honesty guard, and the misses it explains."""

from __future__ import annotations

from typing import Any

from personal_os_worker.jobs.resolve import Knowledge, Resolver, VerdictResolver
from personal_os_worker.jobs.short_answers import Verdict

YES_NO = {"tag": "select", "options": ["Yes", "No"]}


def knowledge(
    facts: dict[str, Any],
    patterns: list[dict[str, Any]] | None = None,
    archive: list[dict[str, Any]] | None = None,
) -> Knowledge:
    return Knowledge.build(
        facts=[{"key": key, "value": value} for key, value in facts.items()],
        career=None,
        patterns=patterns or [],
        archive=archive or [],
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
    # Odd binding, but it is what was accepted: the binding wins over the archive.
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
    # Two expressions naming different facts: neither wins; the archive does.
    assert ask(resolver, "Would Acme need to sponsor you?").source == "archive"


def test_the_archive_fills_from_the_fact_and_names_its_rule() -> None:
    resolver = Resolver(knowledge({"work_auth.requires_sponsorship": False}))
    result = ask(resolver, "Will you now or in the future require visa sponsorship?")
    assert (result.value, result.source, result.resolver_id, result.fact_key) == (
        "No", "archive", "archive:builtin:sponsorship", "work_auth.requires_sponsorship"
    )


def test_an_old_answer_that_disagrees_with_the_fact_blocks_the_archive() -> None:
    archive = [{"id": "a1", "normalized_key": "what is your anticipated graduation month year",
                "answer_text": "May 2028", "reuse_policy": "exact"}]
    resolver = Resolver(knowledge({"education.graduation_date": "2027-12-15"}, archive=archive))
    question = "What is your anticipated graduation month/year?"
    assert ask(resolver, question, {"tag": "input", "type": "text"}) is None
    miss = resolver.misses["what is your anticipated graduation month year"]
    assert miss.stage == "pattern" and miss.fact_key == "education.graduation_date"
    assert "May 2028" in miss.note and "December 2027" in miss.note


def test_an_old_answer_in_another_rendering_of_the_fact_agrees() -> None:
    archive = [{"id": "a1", "normalized_key": "what is your expected graduation date",
                "answer_text": "Fall 2027", "reuse_policy": "exact"},
               {"id": "a2", "normalized_key": "veteran status",
                "answer_text": "I am not a protected veteran", "reuse_policy": "exact"}]
    resolver = Resolver(knowledge(
        {"education.graduation_date": "2027-12-15", "eeo.veteran_status": "No"}, archive=archive
    ))
    result = ask(resolver, "What is your expected graduation date?", {"tag": "input"})
    assert result.value == "December 2027"
    options = {"tag": "select", "options": ["I am a protected veteran",
                                            "I am not a protected veteran"]}
    assert ask(resolver, "Veteran Status", options).value == "I am not a protected veteran"


def test_misses_say_which_fact_is_missing_or_what_did_not_fit() -> None:
    resolver = Resolver(knowledge({"education.gpa": 3.78}))
    assert ask(resolver, "Are you at least 18 years of age?") is None
    adult = resolver.misses["are you at least 18 years of age"]
    assert (adult.stage, adult.fact_key) == ("pattern", "identity.adult")
    assert "no such fact" in adult.note

    assert ask(resolver, "What is your GPA?", {"tag": "select", "options": ["A", "B"]}) is None
    gpa = resolver.misses["what is your gpa"]
    assert (gpa.stage, gpa.fact_key) == ("format", "education.gpa")


def test_a_hint_with_no_fact_is_only_a_hint() -> None:
    archive = [{"id": "a1", "normalized_key": "search", "answer_text": "python async",
                "reuse_policy": "contextual"}]
    resolver = Resolver(knowledge({}, archive=archive))
    assert ask(resolver, "Search", {"tag": "input"}) is None
    assert "python async" in resolver.misses["search"].note


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


def test_agreement_is_by_whole_words_and_notes_show_both_values() -> None:
    from personal_os_worker.jobs.formatters import Rendering
    from personal_os_worker.jobs.resolve import agrees

    assert not agrees(Rendering("Example Institute of Technology", "text_as_is"), [], ["No"])
    assert agrees(Rendering("I am not a protected veteran", "boolean_as_option"), [],
                  ["I am not a protected veteran"])
    archive = [{"id": "a1", "normalized_key": "disability status", "answer_text": "Yes",
                "reuse_policy": "exact"}]
    rows = [{"key": "eeo.disability_status", "value": "No", "sensitive": True}]
    resolver = Resolver(Knowledge.build(facts=rows, career=None, archive=archive))
    assert ask(resolver, "Disability Status", {"tag": "input"}) is None
    note = resolver.misses["disability status"].note
    assert "'Yes'" in note and "'No'" in note
