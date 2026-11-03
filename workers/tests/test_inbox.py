"""`os hunt inbox`: the human step of the loop, against a recording API."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

from personal_os_worker.jobs.inbox import (
    print_waiting_facts,
    propose_expression,
    walk_inbox,
)

TODAY = date(2026, 10, 14)


def candidate(**overrides: Any) -> dict[str, Any]:
    pattern = {
        "id": "p1",
        "kind": "binding",
        "question_id": "q1",
        "question_label": "Will you require sponsorship?",
        "fact_key": "work_auth.requires_sponsorship",
        "origin": "model",
        "status": "candidate",
    } | overrides
    return {
        "pattern": pattern,
        "answers": [{"company_name": "DoorDash", "answer_text": "No", "source": "model",
                     "submitted": True}],
    }


def question(label: str, qid: str = "q2", answer: str = "Yes") -> dict[str, Any]:
    return {
        "question": {"id": qid, "sample_label": label, "disposition": "unbound"},
        "answers": [{"company_name": "Acme", "answer_text": answer, "source": "manual",
                     "submitted": True}],
    }


class FakeApi:
    def __init__(self, inbox: dict[str, Any], *, expressions: list[str] = ()) -> None:  # type: ignore[assignment]
        self._inbox = inbox
        self._expressions = expressions
        self.calls: list[tuple[str, Any]] = []
        self.facts_rows = [
            {"key": "work_auth.requires_sponsorship", "value": False, "sensitive": False},
            {"key": "contact.phone", "value": "5550100199", "sensitive": True},
        ]

    def inbox(self) -> dict[str, Any]:
        return self._inbox

    def autofill_failures(self) -> list[dict[str, Any]]:
        return [{"id": "f1", "status": "repaired"}, {"id": "f2", "status": "open"}]

    def facts(self) -> list[dict[str, Any]]:
        return self.facts_rows

    def career(self) -> dict[str, Any]:
        raise RuntimeError("No career record yet")

    def sightings(self) -> list[dict[str, Any]]:
        return []

    def patterns(self, *statuses: str) -> list[dict[str, Any]]:
        return [{"kind": "expression", "expression": pattern} for pattern in self._expressions]

    def update_pattern(self, pattern_id: str, *, status: str, origin: str) -> dict[str, Any]:
        self.calls.append(("pattern", (pattern_id, status, origin)))
        return {}

    def create_pattern(self, **fields: Any) -> dict[str, Any]:
        self.calls.append(("create", fields))
        return {}

    def update_question(self, question_id: str, *, disposition: str) -> dict[str, Any]:
        self.calls.append(("question", (question_id, disposition)))
        return {}

    def set_fact(self, key: str, value: Any, **fields: Any) -> dict[str, Any]:
        self.calls.append(("fact", (key, value, fields)))
        return {}

    def delete_prose(self, prose_id: str) -> None:
        self.calls.append(("delete prose", prose_id))


def answers(*replies: str) -> Any:
    replies_iter: Iterator[str] = iter(replies)
    return lambda _prompt: next(replies_iter)


def test_accepting_a_candidate_activates_it() -> None:
    api = FakeApi({"candidates": [candidate()], "questions": [], "prose": []})
    counts = walk_inbox(api, ask=answers("a"), today=TODAY)  # type: ignore[arg-type]
    assert api.calls == [("pattern", ("p1", "active", "inbox"))]
    assert counts["accepted"] == 1


def test_rejecting_a_candidate_retires_it_and_other_letters_reprompt() -> None:
    api = FakeApi({"candidates": [candidate()], "questions": [], "prose": []})
    counts = walk_inbox(api, ask=answers("w", "r"), today=TODAY)  # type: ignore[arg-type]
    assert api.calls == [("pattern", ("p1", "rejected", "inbox"))]
    assert counts["rejected"] == 1


def test_binding_a_question_creates_the_missing_fact_from_the_catalog() -> None:
    api = FakeApi({"candidates": [], "questions": [question("Are you able to work on-site?")],
                   "prose": []})
    # `?` lists the catalog, then the key, then the suggested value (your last answer).
    counts = walk_inbox(  # type: ignore[arg-type]
        api, ask=answers("b", "?", "availability.onsite", "", ""), today=TODAY
    )
    fact = next(call for call in api.calls if call[0] == "fact")
    assert fact[1][0] == "availability.onsite" and fact[1][1] is True
    assert fact[1][2]["value_type"] == "boolean"
    assert fact[1][2]["label"] == "Available to work on-site or in person"
    created = next(call for call in api.calls if call[0] == "create")[1]
    assert created["kind"] == "binding" and created["question_id"] == "q2"
    assert created["origin"] == "inbox" and "archive_ref" not in created
    assert created["status"] == "active"
    assert counts["bound"] == 1


def test_binding_to_an_existing_fact_does_not_touch_it() -> None:
    api = FakeApi({"candidates": [], "questions": [question("Visa needs?")], "prose": []})
    walk_inbox(  # type: ignore[arg-type]
        api, ask=answers("b", "work_auth.requires_sponsorship", ""), today=TODAY
    )
    assert [call[0] for call in api.calls] == ["create"]
    assert api.calls[0][1]["origin"] == "inbox"


def test_binding_to_a_computed_key_creates_no_fact() -> None:
    api = FakeApi({"candidates": [], "questions": [question("What is your GPA?")], "prose": []})
    walk_inbox(  # type: ignore[arg-type]
        api, ask=answers("b", "education.gpa", ""), today=TODAY
    )
    assert [call[0] for call in api.calls] == ["create"]
    created = api.calls[0][1]
    assert created["kind"] == "binding" and created["fact_key"] == "education.gpa"


def test_one_off_long_form_and_skip_are_dispositions() -> None:
    api = FakeApi({
        "candidates": [],
        "questions": [question("Favorite color?", "q3"), question("Why us?", "q4"),
                      question("Shoe size?", "q5")],
        "prose": [],
    })
    counts = walk_inbox(api, ask=answers("o", "l", "s"), today=TODAY)  # type: ignore[arg-type]
    assert api.calls == [("question", ("q3", "one_off")), ("question", ("q4", "long_form"))]
    assert counts["skipped"] == 1


def test_a_question_an_active_expression_covers_is_not_asked() -> None:
    api = FakeApi(
        {"candidates": [], "questions": [question("Do you need sponsorship?")]},
        expressions=[r"\bsponsor(ship)?\b"],
    )
    counts = walk_inbox(api, ask=answers(), today=TODAY)  # type: ignore[arg-type]
    assert api.calls == [] and sum(counts.values()) == 0


def test_a_question_proposes_the_word_it_asks_about() -> None:
    assert propose_expression(
        "Will you now or in the future require sponsorship for employment visa status?"
    ) == "sponsorship"
    assert propose_expression("What is your expected graduation date?") == "graduation"
    assert propose_expression("Do you have an active security clearance?") == "clearance"
    # Nothing distinctive to generalize on, so nothing is proposed.
    assert propose_expression("Your name") == ""


def test_binding_can_widen_into_an_expression_that_covers_unmet_questions() -> None:
    api = FakeApi({"candidates": [], "questions": [question("Visa needs?")], "prose": []})
    counts = walk_inbox(  # type: ignore[arg-type]
        api, ask=answers("b", "work_auth.requires_sponsorship", "sponsorship"), today=TODAY
    )
    created = [call[1] for call in api.calls if call[0] == "create"]
    assert created[0]["kind"] == "binding"
    assert created[1] == {
        "kind": "expression",
        "expression": "sponsorship",
        "fact_key": "work_auth.requires_sponsorship",
        "origin": "inbox",
        "status": "active",
    }
    assert counts["widened"] == 1


def test_a_proposal_is_taken_with_y_and_a_broken_regex_is_refused() -> None:
    api = FakeApi({"candidates": [], "questions": [question("Sponsorship needed?")], "prose": []})
    walk_inbox(  # type: ignore[arg-type]
        api, ask=answers("b", "work_auth.requires_sponsorship", "y"), today=TODAY
    )
    # Expressions are compiled case-insensitively, so the proposal is lowercase.
    assert [call[1] for call in api.calls if call[0] == "create"][1]["expression"] == "sponsorship"

    broken = FakeApi({"candidates": [], "questions": [question("Visa needs?")], "prose": []})
    counts = walk_inbox(  # type: ignore[arg-type]
        broken, ask=answers("b", "work_auth.requires_sponsorship", "wha(t"), today=TODAY
    )
    assert [call[0] for call in broken.calls] == ["create"]
    assert counts["widened"] == 0


def test_a_missing_computed_fact_names_what_to_change(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_waiting_facts([
        {"reason": "missing", "key": "education.gpa", "question": "GPA?"},
        {"reason": "missing", "key": "contact.phone", "question": "Phone?"},
    ])
    out = capsys.readouterr().out
    assert "missing fact: education.gpa (asked by 'GPA?'); education.gpa is computed " in out
    assert "os hunt resume put" in out
    assert "missing fact: contact.phone (asked by 'Phone?'); 'os hunt facts set' gives it" in out
