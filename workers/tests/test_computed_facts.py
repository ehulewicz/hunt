"""The computed-fact list agrees with the catalog and with the backend's copy.

The backend cannot import the worker, so `jobs_facts_computed.py` mirrors the
worker's declarations and the same `computed_refusal` in standard-library-only
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
    COMPUTED_FACTS,
    DERIVED_FACTS,
    POSTING_FACTS,
    computed_refusal,
)

BACKEND_COMPUTED = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "src"
    / "personal_os_backend"
    / "jobs_facts_computed.py"
)

REFUSALS = {
    "education.gpa": "education.gpa is computed from the career record; "
    "edit the record at /resume/edit or with os hunt resume put",
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
    spec = importlib.util.spec_from_file_location("jobs_facts_computed", BACKEND_COMPUTED)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_computed_facts_match_the_catalog_and_are_declared_once() -> None:
    computed = {key: source for key, (source, _item) in COMPUTED_FACTS.items()}
    catalog = {key: entry.source for key, entry in CATALOG.items() if entry.source != "stored"}
    assert set(computed) == set(catalog)
    for key, source in catalog.items():
        if source == "career":
            assert computed[key] == "career", key
        else:
            assert computed[key] in {"derived", "posting"}, key
    assert len(COMPUTED_FACTS) == len(CAREER_FACTS) + len(DERIVED_FACTS) + len(POSTING_FACTS)


@pytest.mark.parametrize(("key", "expected"), list(REFUSALS.items()))
def test_computed_refusal_names_what_to_change(key: str, expected: str) -> None:
    assert computed_refusal(key) == expected


@pytest.mark.parametrize("key", ["contact.phone", "custom.anything"])
def test_computed_refusal_leaves_keys_it_does_not_own(key: str) -> None:
    assert computed_refusal(key) is None


def test_backend_computed_facts_and_refusals_match_the_worker() -> None:
    backend = _backend_module()
    expected = {
        key: (source, CATALOG[key].label, item.reads)
        for key, (source, item) in COMPUTED_FACTS.items()
    }
    assert expected == backend.COMPUTED_FACTS
    for key in (*COMPUTED_FACTS, "contact.phone"):
        assert backend.computed_refusal(key) == computed_refusal(key)
