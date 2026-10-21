from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from personal_os_worker.cli import build_parser, expand_shorthand
from personal_os_worker.jobs.facts.commands import (
    library_conflicts,
    none_value,
    parse_value,
    review_facts,
)
from personal_os_worker.jobs.facts.derived import completed_rotations, derive, graduation_term

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


@pytest.mark.parametrize(
    ("today", "age", "adult"),
    [(date(2023, 2, 25), 17, False), (date(2023, 2, 26), 18, True), (date(2026, 10, 17), 21, True)],
)
def test_age_and_adulthood_follow_the_birth_date(today: date, age: int, adult: bool) -> None:
    derived = derive({"identity.birth_date": "2005-02-26"}, today)
    assert (derived["identity.age"], derived["identity.adult"]) == (age, adult)


def test_one_line_answers_are_derived_from_their_parts() -> None:
    derived = derive({
        "work_auth.us_citizen": True, "address.line_1": "100 Example Ave",
        "address.city": "Atlanta", "address.state": "Georgia", "address.postal_code": "00000",
        "education.degree_type": "BS", "education.field_of_study": "Computer Science",
    }, date(2026, 10, 17))
    assert derived["work_auth.status"] == "US Citizen"
    assert derived["address.city_state_zip"] == "Atlanta, Georgia 00000"
    assert derived["address.full"] == "100 Example Ave, Atlanta, Georgia 00000"
    assert derived["education.degree_and_field"] == "BS in Computer Science"


def test_applied_before_counts_only_applications_that_went_in() -> None:
    from personal_os_worker.jobs.facts.derived import posting_facts

    assert posting_facts({}, "Point72", [{"status": "applied"}])[
        "employment.applied_before"
    ] is True
    assert posting_facts({}, "Point72", [{"status": "skipped"}, {"status": "applying"}])[
        "employment.applied_before"
    ] is False
    assert "employment.applied_before" not in posting_facts({}, "Point72")


@pytest.mark.parametrize(
    ("title", "dates", "term", "conflict"),
    [
        ("Software Engineering Intern - Summer 2027", "May 18, 2027 \u2013 August 10, 2027",
         "Summer 2027", False),
        ("Co-op - Spring/Summer 2027", "January 11, 2027 \u2013 April 5, 2027", "Spring 2027",
         False),
        ("Intern - Fall 2027", "August 31, 2027 \u2013 November 23, 2027", "Fall 2027", True),
        ("Backend Intern", "November 1, 2026 \u2013 January 24, 2027", None, False),
    ],
)
def test_the_window_starts_two_weeks_out_or_at_the_postings_term(
    title: str, dates: str, term: str | None, conflict: bool
) -> None:
    from personal_os_worker.jobs.facts.derived import posting_facts

    found = posting_facts(
        {"availability.notice_period_weeks": 2, "education.graduation_date": "2027-12-01"},
        "Acme", title=title, today=date(2026, 10, 18),
    )
    assert found["availability.internship_dates"] == dates
    assert found.get("availability.term") == term
    assert found["availability.conflicts_with_school"] is conflict


def test_a_referral_belongs_to_the_application() -> None:
    from personal_os_worker.jobs.facts.derived import posting_facts

    plain = posting_facts({}, "Amazon", title="SDE Intern", today=date(2026, 10, 18))
    assert plain["referral.referred"] is False and "referral.referrer_name" not in plain
    named = posting_facts({}, "Amazon", title="SDE Intern", today=date(2026, 10, 18),
                          referral={"referred": True, "name": "Jane Doe"})
    assert named["referral.referred"] is True and named["referral.referrer_name"] == "Jane Doe"


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


def test_knowledge_presents_stored_derived_and_posting_facts() -> None:
    from personal_os_worker.jobs.resolve import Knowledge

    facts = [
        fact("identity.legal_name", "Ada Lovelace"),
        fact("address.city", "Atlanta"),
        fact("address.state", "Georgia"),
        fact("address.country", "United States"),
        fact("work_auth.us_citizen", True) | {"value_type": "boolean"},
        fact("education.graduation_date", "2027-12-15") | {"value_type": "date"},
        fact("coop.anchor", ANCHOR) | {"value_type": "object"},
        fact("compensation.minimum", {"amount": 40, "currency": "USD", "period": "hour"})
        | {"value_type": "object"},
        fact("contact.phone", "5550001111"),
    ]
    knowledge = Knowledge.build(
        facts=facts, career=CAREER, company="Northwind", today=date(2027, 5, 1)
    )
    assert knowledge.facts["identity.first_name"] == "Ada"
    assert knowledge.facts["identity.last_name"] == "Lovelace"
    assert knowledge.facts["address.location"] == "Atlanta, Georgia, United States"
    assert knowledge.facts["work_auth.citizenship_country"] == "United States"
    assert knowledge.facts["education.graduation_term"] == "Fall"
    assert knowledge.facts["coop.completed_rotations"] == 6
    assert knowledge.facts["compensation.minimum_annual"] == 83200
    assert knowledge.facts["employment.worked_for"] is True
    assert knowledge.types["education.graduation_date"] == "date"
    assert knowledge.types["employment.worked_for"] == "boolean"
    # The model sees every key, label, and value; nothing is withheld.
    shown = {item["key"]: item["value"] for item in knowledge.for_model()}
    assert shown["contact.phone"] == "5550001111"
    assert knowledge.values()["contact.phone"] == "5550001111"


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

    def archive_answers(self) -> list[dict[str, Any]]:
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
         "--label", "Holds a security clearance"]
    )
    assert (args.key, args.value, args.value_type) == (
        "work_auth.security_clearance", "no", "boolean"
    )


CAREER = {
    "education": {
        "school": "Example Institute of Technology",
        "degree_type": "BS",
        "field_of_study": "Computer Science",
        "graduation_date": "2027-12-01",
        "gpa": "3.78",
    },
    "entries": [
        {"section": "experience", "title": "Middleware Engineer", "org": "Northwind",
         "employment_type": "coop", "start_date": "2025-05-01", "end_date": None},
        {"section": "experience", "title": "Technical Co-founder", "org": "Northhealth AI",
         "employment_type": "founder", "start_date": "2025-10-01", "end_date": "2026-05-01"},
        {"section": "experience", "title": "Software Engineer", "org": "Example Public Schools",
         "employment_type": "contract", "start_date": "2023-05-01", "end_date": "2023-12-01"},
        {"section": "research", "title": "Example Lab @ Example University",
         "org": "Example Nuclear Group", "employment_type": None,
         "job_title": "Undergraduate Researcher", "employer": "Example Lab @ Example University",
         "start_date": "2024-04-01", "end_date": "2024-09-01"},
        {"section": "project", "title": "AlphaResearch", "org": None, "employment_type": None,
         "start_date": None, "end_date": "2026-06-01"},
    ],
    "skills": [
        {"category": "Languages", "items": ["Python", "Java"]},
        {"category": "Tools", "items": ["Git", "Python"]},
    ],
}


def test_career_facts_follow_the_resume() -> None:
    from personal_os_worker.jobs.facts.derived import career_facts

    facts = career_facts(CAREER, date(2026, 10, 14))
    assert facts["education.degree_type"] == "BS"
    assert facts["education.graduation_date"] == "2027-12-01"
    assert facts["education.gpa"] == 3.78
    assert facts["employment.current_employer"] == "Northwind"
    assert facts["employment.current_title"] == "Middleware Engineer"
    assert facts["employment.current_start_date"] == "2025-05-01"
    assert facts["employment.organizations"] == [
        "Northwind", "Northhealth AI", "Example Public Schools",
        "Example Nuclear Group", "Example Lab @ Example University",
    ]
    assert facts["skills.all"] == ["Python", "Java", "Git"]


def test_a_form_gets_the_entrys_employer_and_job_title_over_its_heading() -> None:
    from personal_os_worker.jobs.facts.derived import career_facts

    entries = [
        {"section": "experience", "title": "Founding Engineer", "org": "Acme Labs",
         "employer": "Acme Inc.", "job_title": "Software Engineer",
         "employment_type": "full_time", "start_date": "2026-01-01", "end_date": None},
    ]
    facts = career_facts({"entries": entries}, date(2026, 10, 14))
    assert facts["employment.current_employer"] == "Acme Inc."
    assert facts["employment.current_title"] == "Software Engineer"
    assert facts["employment.organizations"] == ["Acme Labs", "Acme Inc."]


@pytest.mark.parametrize(
    ("today", "years"),
    [
        # May-Dec 2023 (245 days) plus May 2025 to today; Northhealth overlaps.
        (date(2026, 10, 14), 2),
        (date(2026, 3, 1), 1),
        (date(2028, 1, 1), 3),
    ],
)
def test_tenure_is_the_union_of_experience_spans_in_whole_years(today: date, years: int) -> None:
    from personal_os_worker.jobs.facts.derived import years_of_experience

    assert years_of_experience(CAREER["entries"], today) == years  # type: ignore[arg-type]


def test_research_and_projects_never_count_toward_tenure() -> None:
    from personal_os_worker.jobs.facts.derived import years_of_experience

    entries = [entry for entry in CAREER["entries"] if entry["section"] != "experience"]
    assert years_of_experience(entries, date(2030, 1, 1)) is None  # type: ignore[arg-type]


def test_a_stored_fact_shadows_the_career_value_and_is_named() -> None:
    from personal_os_worker.jobs.facts.derived import career_facts, shadowed
    from personal_os_worker.jobs.resolve import Knowledge

    from_career = career_facts(CAREER, date(2026, 10, 14))
    stored = {"education.degree_type": "Bachelor's", "experience.years_professional": 4,
              "education.school_name": "Example Institute of Technology"}
    assert shadowed(stored, from_career) == {
        "education.degree_type": "BS", "experience.years_professional": 2,
    }
    knowledge = Knowledge.build(
        facts=[{"key": key, "value": value} for key, value in stored.items()],
        career=CAREER,
        today=date(2026, 10, 14),
    )
    assert knowledge.facts["education.degree_type"] == "Bachelor's"
    assert knowledge.facts["experience.years_professional"] == 4
    assert knowledge.facts["education.field_of_study"] == "Computer Science"  # the career decides


@pytest.mark.parametrize(
    ("preferred", "legal", "same"),
    [("Ethan", "Alex Example", True), ("ethan", "Alex Quinn Example", True),
     ("Eddie", "Alex Example", False)],
)
def test_whether_the_preferred_name_is_the_legal_one(
    preferred: str, legal: str, same: bool
) -> None:
    facts = {"identity.preferred_name": preferred, "identity.legal_name": legal}
    assert derive(facts, date(2026, 10, 20))["identity.preferred_is_legal"] is same
    assert "identity.preferred_is_legal" not in derive(
        {"identity.legal_name": legal}, date(2026, 10, 20)
    )


def test_a_none_fact_is_the_empty_value_of_its_type() -> None:
    assert none_value("text") == ""
    assert none_value("list") == []
    with pytest.raises(ValueError):
        none_value("boolean")


def test_facts_set_takes_a_value_or_none() -> None:
    args = build_parser().parse_args(["hunt", "facts", "set", "address.line_2", "--none"])
    assert (args.key, args.value, args.none) == ("address.line_2", None, True)
