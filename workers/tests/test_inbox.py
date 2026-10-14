"""`os hunt inbox`: the human step of the loop, against a recording API."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

from personal_os_worker.jobs.inbox import walk_inbox

TODAY = date(2026, 10, 14)


def candidate(**overrides: Any) -> dict[str, Any]:
    pattern = {
        "id": "p1",
        "kind": "binding",
        "question_id": "q1",
        "question_label": "Will you require sponsorship?",
        "fact_key": "work_auth.requires_sponsorship",
        "origin": "archive",
        "archive_ref": "archive:builtin:sponsorship",
        "status": "candidate",
    } | overrides
    return {
        "pattern": pattern,
        "answers": [{"company_name": "DoorDash", "answer_text": "No", "source": "archive",
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

    def facts(self) -> list[dict[str, Any]]:
        return self.facts_rows

    def career(self) -> dict[str, Any]:
        raise RuntimeError("No career record yet")

    def archive_answers(self) -> list[dict[str, Any]]:
        return [{"id": "a1", "normalized_key": "are you at least 18 years of age",
                 "answer_text": "Yes", "reuse_policy": "exact"}]

    def patterns(self, *statuses: str) -> list[dict[str, Any]]:
        return [{"kind": "expression", "expression": pattern} for pattern in self._expressions]

    def update_pattern(self, pattern_id: str, *, status: str) -> dict[str, Any]:
        self.calls.append(("pattern", (pattern_id, status)))
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

    def update_prose(self, prose_id: str, **fields: Any) -> dict[str, Any]:
        self.calls.append(("prose", (prose_id, fields)))
        return {}

    def delete_prose(self, prose_id: str) -> None:
        self.calls.append(("delete prose", prose_id))


def answers(*replies: str) -> Any:
    replies_iter: Iterator[str] = iter(replies)
    return lambda _prompt: next(replies_iter)


def test_accepting_a_candidate_activates_it() -> None:
    api = FakeApi({"candidates": [candidate()], "questions": [], "prose": []})
    counts = walk_inbox(api, ask=answers("a"), today=TODAY)  # type: ignore[arg-type]
    assert api.calls == [("pattern", ("p1", "active"))]
    assert counts["accepted"] == 1


def test_widening_adopts_the_archived_rule_expression() -> None:
    api = FakeApi({"candidates": [candidate()], "questions": [], "prose": []})
    counts = walk_inbox(api, ask=answers("w"), today=TODAY)  # type: ignore[arg-type]
    assert api.calls[0] == ("pattern", ("p1", "active"))
    kind, fields = api.calls[1]
    assert kind == "create" and fields["kind"] == "expression"
    assert fields["expression"] == r"\bsponsor(ship)?\b"
    assert fields["archive_ref"] == "archive:builtin:sponsorship" and fields["status"] == "active"
    assert counts["widened"] == 1


def test_a_model_candidate_cannot_be_widened() -> None:
    api = FakeApi(
        {"candidates": [candidate(origin="model", archive_ref=None)], "questions": [], "prose": []}
    )
    walk_inbox(api, ask=answers("w", "r"), today=TODAY)  # type: ignore[arg-type]
    assert api.calls == [("pattern", ("p1", "rejected"))]


def test_binding_a_question_creates_the_missing_fact_from_the_catalog() -> None:
    api = FakeApi({"candidates": [], "questions": [question("Are you at least 18 years of age?")],
                   "prose": []})
    # Accept the archive's suggested key, then the suggested value (your last answer).
    counts = walk_inbox(api, ask=answers("b", "", ""), today=TODAY)  # type: ignore[arg-type]
    fact = next(call for call in api.calls if call[0] == "fact")
    assert fact[1][0] == "identity.adult" and fact[1][1] is True
    assert fact[1][2]["value_type"] == "boolean" and fact[1][2]["label"] == "At least 18 years old"
    created = next(call for call in api.calls if call[0] == "create")[1]
    assert created["kind"] == "binding" and created["question_id"] == "q2"
    assert created["origin"] == "archive" and created["archive_ref"] == "archive:builtin:adult"
    assert created["status"] == "active"
    assert counts["bound"] == 1


def test_binding_to_an_existing_fact_does_not_touch_it() -> None:
    api = FakeApi({"candidates": [], "questions": [question("Visa needs?")], "prose": []})
    walk_inbox(  # type: ignore[arg-type]
        api, ask=answers("b", "work_auth.requires_sponsorship"), today=TODAY
    )
    assert [call[0] for call in api.calls] == ["create"]
    assert api.calls[0][1]["origin"] == "inbox"


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
        {"candidates": [], "questions": [question("Do you need sponsorship?")], "prose": []},
        expressions=[r"\bsponsor(ship)?\b"],
    )
    counts = walk_inbox(api, ask=answers(), today=TODAY)  # type: ignore[arg-type]
    assert api.calls == [] and sum(counts.values()) == 0


def test_prose_is_approved_or_deleted_and_quit_stops() -> None:
    api = FakeApi({"candidates": [], "questions": [], "prose": [
        {"id": "x1", "question_label": "Why us?", "company_name": "Figma", "text": "Because."},
        {"id": "x2", "question_label": "Why them?", "company_name": "Veeam", "text": "Hmm."},
        {"id": "x3", "question_label": "Why?", "company_name": "Exegy", "text": "Never asked."},
    ]})
    walk_inbox(api, ask=answers("a", "d", "q"), today=TODAY)  # type: ignore[arg-type]
    assert api.calls == [("prose", ("x1", {"approved": True})), ("delete prose", "x2")]
