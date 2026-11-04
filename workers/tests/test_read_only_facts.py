"""The read-only fact list agrees with the catalog and with the backend's copy.

The backend cannot import the worker, so `jobs_facts_read_only.py` mirrors the
worker's declarations and the same `read_only_refusal` in standard-library-only
code; this test loads it by path and compares them directly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from personal_os_worker.jobs.facts.catalog import CATALOG
from personal_os_worker.jobs.facts.derived import (
    CAREER_FACTS,
    DERIVED_FACTS,
    POSTING_FACTS,
    READ_ONLY_FACTS,
    read_only_refusal,
)

BACKEND_READ_ONLY = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "src"
    / "personal_os_backend"
    / "jobs_facts_read_only.py"
)

RECORD_FACTS = {
    "education.school_name",
    "education.degree_type",
    "education.field_of_study",
    "education.graduation_date",
    "education.gpa",
    "education.coursework",
    "education.high_school_name",
    "education.high_school_city",
    "education.high_school_graduation_year",
    "employment.current_employer",
    "employment.current_title",
    "employment.current_start_date",
    "employment.organizations",
    "skills.all",
}

REFUSALS = {
    "education.gpa": "education.gpa comes from the career record; "
    "edit the record at /resume/edit or with os hunt resume put",
    "experience.years_professional": "experience.years_professional is computed "
    "from the career record; edit the record at /resume/edit or with "
    "os hunt resume put",
    "education.graduation_term": "education.graduation_term is computed from "
    "education.graduation_date; edit education.graduation_date in the career "
    "record at /resume/edit",
    "identity.first_name": "identity.first_name is computed from "
    "identity.legal_name; set identity.legal_name",
    "education.highest_completed": "education.highest_completed is computed from "
    "education.degree_level, education.currently_enrolled; edit "
    "education.degree_type, education.graduation_date in the career record "
    "at /resume/edit",
    "availability.conflicts_with_school": "availability.conflicts_with_school is "
    "computed from availability.internship_dates, education.graduation_date; "
    "edit education.graduation_date in the career record at /resume/edit; "
    "set availability.notice_period_weeks",
    "availability.term": "availability.term is computed from the posting's title; "
    "it is decided for each application",
    "referral.referrer_name": "referral.referrer_name is set for one application "
    "with os hunt apply --referred NAME",
}


def _backend_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("jobs_facts_read_only", BACKEND_READ_ONLY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_read_only_facts_match_the_catalog_and_are_declared_once() -> None:
    read_only = {key: source for key, (source, _item) in READ_ONLY_FACTS.items()}
    catalog = {key: entry.source for key, entry in CATALOG.items() if entry.source != "stored"}
    assert set(read_only) == set(catalog)
    for key, source in catalog.items():
        if source == "career":
            assert read_only[key] == "career", key
        else:
            assert read_only[key] in {"derived", "posting"}, key
    assert (
        len(READ_ONLY_FACTS)
        == len(CAREER_FACTS) + len(DERIVED_FACTS) + len(POSTING_FACTS)
        == 53
    )


def test_the_static_facts_are_exactly_the_record_facts() -> None:
    assert {
        key for key, (_source, item) in READ_ONLY_FACTS.items() if item.kind == "static"
    } == RECORD_FACTS
    assert all(READ_ONLY_FACTS[key][0] == "career" for key in RECORD_FACTS)


@pytest.mark.parametrize(("key", "expected"), list(REFUSALS.items()))
def test_read_only_refusal_names_what_to_change(key: str, expected: str) -> None:
    assert read_only_refusal(key) == expected


@pytest.mark.parametrize("key", ["contact.phone", "custom.anything"])
def test_read_only_refusal_leaves_keys_it_does_not_own(key: str) -> None:
    assert read_only_refusal(key) is None


def test_backend_read_only_facts_and_refusals_match_the_worker() -> None:
    backend = _backend_module()
    expected = {
        key: (source, item.kind, CATALOG[key].label, item.reads)
        for key, (source, item) in READ_ONLY_FACTS.items()
    }
    assert expected == backend.READ_ONLY_FACTS
    for key in (*READ_ONLY_FACTS, "contact.phone"):
        assert backend.read_only_refusal(key) == read_only_refusal(key)
