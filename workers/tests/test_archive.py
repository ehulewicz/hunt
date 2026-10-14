"""The archive: what the old system knew, translated to facts, never typed (R30, R41)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_os_worker.jobs.archive.lookup import lookup, normalize_label, translate
from personal_os_worker.jobs.archive.rules import RULES
from personal_os_worker.jobs.facts.catalog import CATALOG

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "archive_translations.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    "case", CORPUS, ids=[" ".join(case["question"].split())[:50] for case in CORPUS]
)
def test_every_archived_question_translates_to_the_fact_it_did(case: dict[str, str]) -> None:
    rule = translate(case["question"])
    assert (rule.fact_key if rule else None) == case["fact_key"]
    assert (rule.ref if rule else None) == case["rule"]


def test_the_corpus_covers_the_old_phrasings_of_the_common_facts() -> None:
    by_fact: dict[str, int] = {}
    for case in CORPUS:
        if case["fact_key"]:
            by_fact[case["fact_key"]] = by_fact.get(case["fact_key"], 0) + 1
    assert by_fact["work_auth.requires_sponsorship"] >= 15
    assert by_fact["education.graduation_date"] >= 7
    assert by_fact["work_auth.authorized_us"] >= 9


def test_every_rule_names_a_catalog_fact() -> None:
    assert {rule.fact_key for rule in RULES} <= set(CATALOG)
    assert len({rule.ref for rule in RULES}) == len(RULES)


def test_an_earlier_tier_decides_before_a_later_one() -> None:
    # The exact label wins over the builtin that also matches it.
    assert translate("LinkedIn").ref == "label:linkedin"  # type: ignore[union-attr]
    assert translate("Your LinkedIn Profile").ref == "builtin:linkedin"  # type: ignore[union-attr]


def test_rules_that_disagree_inside_a_tier_pass_the_question_through() -> None:
    # "major life activities" is the major rule; "disability" is the disability rule.
    assert translate("Do you have a disability that limits major life activities?") is None


def test_a_rule_that_needed_the_options_only_matches_with_them() -> None:
    question = "What is your authorization to work in the US?"
    plain = translate(question)
    assert plain is not None and plain.fact_key == "work_auth.authorized_us"
    with_options = translate(
        question, {"options": ["U.S. Citizen or National", "Permanent Resident", "Other"]}
    )
    assert with_options is not None and with_options.ref == "priority:authorization_option"
    assert translate("End date month") is None
    education = translate("End date month", {"section": "education"})
    assert education is not None and education.formatter == "date_as_month_name"


def test_required_marks_do_not_hide_a_label() -> None:
    assert normalize_label("  Email\u2731 ") == "email"
    assert translate("Phone *").fact_key == "contact.phone"  # type: ignore[union-attr]


def test_old_answers_come_along_as_hints_only() -> None:
    archive = [
        {"id": "a1", "normalized_key": "what is your anticipated graduation month year",
         "answer_text": "May 2028", "reuse_policy": "exact"},
        {"id": "a2", "normalized_key": "search", "answer_text": "python async",
         "reuse_policy": "contextual"},
        {"id": "a3", "normalized_key": r"\bavailable to (work|start)\b", "answer_text": "Yes",
         "reuse_policy": "pattern"},
    ]
    hit = lookup("What is your anticipated graduation month/year?", archive_answers=archive)
    assert hit is not None and hit.fact_key == "education.graduation_date"
    assert [hint.answer for hint in hit.hints] == ["May 2028"]
    assert hit.resolver_id == "archive:priority:graduation"

    junk = lookup("Search", archive_answers=archive)
    assert junk is not None and junk.fact_key is None and junk.rule is None
    assert [hint.answer for hint in junk.hints] == ["python async"]

    assert lookup("Favorite color?", archive_answers=archive) is None
