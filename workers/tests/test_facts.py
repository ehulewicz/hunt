from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from personal_os_worker.cli import build_parser, expand_shorthand
from personal_os_worker.jobs.facts.commands import library_conflicts, parse_value, review_facts
from personal_os_worker.jobs.facts.derived import completed_rotations, derive, graduation_term
from personal_os_worker.jobs.facts.profile import legacy_profile

ANCHOR = {"count": 4, "as_of": "2026-08-01"}
GRADUATION = date(2027, 12, 15)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 8, 1), 4),
        (date(2026, 10, 8), 4),
        (date(2026, 10, 31), 4),
        (date(2026, 11, 1), 5),
        (date(2027, 4, 30), 5),
        (date(2027, 5, 1), 6),
        (date(2027, 8, 1), 7),
        (date(2027, 11, 1), 8),
        (date(2027, 12, 15), 8),
        (date(2029, 6, 1), 8),
    ],
)
def test_completed_rotations_follow_the_boundaries_and_stop_at_graduation(
    today: date, expected: int
) -> None:
    assert completed_rotations(ANCHOR, GRADUATION, today) == expected


def test_completed_rotations_without_a_graduation_keep_counting() -> None:
    assert completed_rotations(ANCHOR, None, date(2028, 8, 1)) == 10


@pytest.mark.parametrize(
    "anchor", [None, {}, {"count": "4", "as_of": "2026-08-01"}, {"count": 4, "as_of": "soon"}]
)
def test_a_malformed_anchor_derives_nothing(anchor: object) -> None:
    assert completed_rotations(anchor, GRADUATION, date(2026, 10, 8)) is None


@pytest.mark.parametrize(
    ("month", "term"), [(5, "Spring"), (6, "Summer"), (8, "Summer"), (9, "Fall"), (12, "Fall")]
)
def test_graduation_term(month: int, term: str) -> None:
    assert graduation_term(date(2027, month, 15)) == term


def test_derive_skips_facts_whose_inputs_are_missing() -> None:
    facts = {
        "coop.anchor": ANCHOR,
        "education.graduation_date": "2027-12-15",
        "compensation.minimum": {"amount": 40, "currency": "USD", "period": "hour"},
    }
    assert derive(facts, date(2026, 10, 8)) == {
        "coop.completed_rotations": 4,
        "education.graduation_term": "Fall",
        "education.currently_enrolled": True,
        "compensation.minimum_annual": 83200,
        "experience.prior_internship": True,
    }
    assert derive({}, date(2026, 10, 8)) == {}


def fact(key: str, value: Any) -> dict[str, Any]:
    return {"key": key, "value": value, "value_type": "text"}


def test_legacy_profile_presents_facts_in_the_old_column_shape() -> None:
    facts = [
        fact("identity.legal_name", "Ada Lovelace"),
        fact("contact.email", "ada@example.com"),
        fact("address.country", "United States"),
        fact("work_auth.us_citizen", True),
        fact("work_auth.authorized_us", True),
        fact("work_auth.requires_sponsorship", False),
        fact("education.graduation_date", "2027-12-15"),
        fact("education.gpa", 3.9),
        fact("availability.notice_period_weeks", 2),
        fact("coop.anchor", ANCHOR),
        fact("compensation.minimum", {"amount": 40, "currency": "USD", "period": "hour"}),
        fact("eeo.race_ethnicities", ["Example"]),
    ]
    profile = legacy_profile(facts, date(2027, 5, 1))
    assert profile["legal_name"] == "Ada Lovelace"
    assert profile["email"] == "ada@example.com"
    assert profile["work_authorization_status"] == "Yes"
    assert profile["visa_status"] == "Citizen"
    assert profile["sponsorship_required"] is False
    assert profile["graduation_date"] == "2027-12-15"
    assert profile["graduation_term"] == "Fall"
    assert profile["completed_coop_terms"] == 6
    assert profile["gpa"] == 3.9
    assert profile["earliest_start_text"] == "Two weeks from offer acceptance."
    assert profile["minimum_salary"] == 40
    assert profile["minimum_salary_period"] == "hour"
    assert profile["eeo_race_ethnicities"] == ["Example"]
    assert profile["target_roles"] == []
    assert profile["phone"] is None


def test_legacy_profile_authorization_without_citizenship() -> None:
    assert legacy_profile([fact("work_auth.authorized_us", False)], date(2026, 10, 8))[
        "work_authorization_status"
    ] == "No"
    profile = legacy_profile([], date(2026, 10, 8))
    assert profile["work_authorization_status"] is None
    assert profile["visa_status"] is None


@pytest.mark.parametrize(
    ("value_type", "raw", "expected"),
    [
        ("text", " Computer Science ", "Computer Science"),
        ("boolean", "Yes", True),
        ("boolean", "n", False),
        ("number", "40", 40),
        ("number", "3.78", 3.78),
        ("date", "2027-12-15", "2027-12-15"),
        ("list", '["White"]', ["White"]),
        ("object", '{"count": 4}', {"count": 4}),
    ],
)
def test_parse_value(value_type: str, raw: str, expected: object) -> None:
    assert parse_value(value_type, raw) == expected


@pytest.mark.parametrize(
    ("value_type", "raw"),
    [("text", "  "), ("boolean", "maybe"), ("number", "forty"), ("date", "Dec 2027"),
     ("list", '{"a": 1}'), ("object", "[1]")],
)
def test_parse_value_rejects_what_does_not_fit(value_type: str, raw: str) -> None:
    with pytest.raises(ValueError):
        parse_value(value_type, raw)


def test_library_conflicts_lists_distinct_answers_to_similar_questions() -> None:
    answers = [
        {"question": "What is your expected graduation date?", "answer_text": "Fall 2027"},
        {"question": "When do you graduate?", "answer_text": "May 2028"},
        {"question": "Graduation date", "answer_text": "Fall 2027"},
        {"question": "Do you need sponsorship?", "answer_text": "No"},
    ]
    assert library_conflicts("education.graduation_date", answers) == [
        ("What is your expected graduation date?", "Fall 2027"),
        ("When do you graduate?", "May 2028"),
    ]
    assert library_conflicts("identity.unlisted", answers) == []


class FakeApi:
    def __init__(self, facts: list[dict[str, Any]]) -> None:
        self.rows = {str(item["key"]): dict(item) for item in facts}
        self.calls: list[tuple[str, str]] = []

    def facts(self) -> list[dict[str, Any]]:
        return list(self.rows.values())

    def answers(self) -> list[dict[str, Any]]:
        return []

    def verify_fact(self, key: str) -> dict[str, Any]:
        self.calls.append(("verify", key))
        self.rows[key]["verified_at"] = "now"
        return self.rows[key]

    def set_fact(self, key: str, value: Any, **_: Any) -> dict[str, Any]:
        self.calls.append(("set", key))
        self.rows[key].update(value=value, verified_at="now")
        return self.rows[key]

    def delete_fact(self, key: str) -> None:
        self.calls.append(("delete", key))
        del self.rows[key]


def test_review_walks_unverified_facts_and_records_each_decision() -> None:
    api = FakeApi([
        {"key": "a.done", "value": "x", "value_type": "text", "verified_at": "earlier"},
        {"key": "a.accept", "value": "x", "value_type": "text", "verified_at": None},
        {"key": "a.edit", "value": 1, "value_type": "number", "verified_at": None},
        {"key": "a.delete", "value": "x", "value_type": "text", "verified_at": None},
        {"key": "a.skip", "value": "x", "value_type": "text", "verified_at": None},
    ])
    replies = iter(["a", "e", "not a number", "e", "2", "d", "s"])
    decided, remaining = review_facts(api, ask=lambda _: next(replies))  # type: ignore[arg-type]
    assert (decided, remaining) == (3, 1)
    assert api.calls == [("verify", "a.accept"), ("set", "a.edit"), ("delete", "a.delete")]
    assert api.rows["a.edit"]["value"] == 2


def test_review_quits_early() -> None:
    api = FakeApi([{"key": "a.one", "value": "x", "value_type": "text", "verified_at": None}])
    assert review_facts(api, ask=lambda _: "q") == (0, 1)  # type: ignore[arg-type]


def test_facts_shorthand_and_parser() -> None:
    assert expand_shorthand(["hunt", "facts"]) == ["hunt", "facts", "list"]
    args = build_parser().parse_args(
        ["hunt", "facts", "set", "work_auth.security_clearance", "no", "--type", "boolean",
         "--label", "Holds a security clearance", "--not-sensitive"]
    )
    assert (args.key, args.value, args.value_type, args.sensitive) == (
        "work_auth.security_clearance", "no", "boolean", False
    )
