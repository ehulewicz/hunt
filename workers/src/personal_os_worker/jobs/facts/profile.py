"""The legacy profile dict, rebuilt from facts.

The resolvers in `autofill.py` and `patterns.py` still read the column names of
the retired `jobs_profile` table. Until the fill-policy slice rewrites them to
read facts directly, this adapter presents facts in exactly that shape, so the
switch of source changes no fill. The values follow the facts, including the
two decided on 2026-10-08: whole co-op rotations derived from the anchor, and a
$40/hour minimum.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from personal_os_worker.jobs.facts.derived import career_facts, derive

# Legacy profile column -> fact key, for values copied unchanged.
DIRECT = {
    "legal_name": "identity.legal_name",
    "preferred_name": "identity.preferred_name",
    "email": "contact.email",
    "phone": "contact.phone",
    "linkedin_url": "contact.linkedin_url",
    "github_url": "contact.github_url",
    "portfolio_url": "contact.portfolio_url",
    "address_line_1": "address.line_1",
    "address_line_2": "address.line_2",
    "city": "address.city",
    "region": "address.region",
    "postal_code": "address.postal_code",
    "country": "address.country",
    "sponsorship_required": "work_auth.requires_sponsorship",
    "school_name": "education.school_name",
    "degree_type": "education.degree_type",
    "field_of_study": "education.field_of_study",
    "graduation_date": "education.graduation_date",
    "gpa": "education.gpa",
    "earliest_start_date": "availability.earliest_start_date",
    "years_professional_experience": "experience.years_professional",
    "remote_preference": "preferences.remote",
    "willing_to_relocate": "preferences.willing_to_relocate",
    "willing_to_travel": "preferences.willing_to_travel",
    "eeo_gender_identity": "eeo.gender_identity",
    "eeo_veteran_status": "eeo.veteran_status",
    "eeo_disability_status": "eeo.disability_status",
}
LISTS = {
    "target_roles": "discovery.target_roles",
    "target_locations": "discovery.target_locations",
    "target_career_stages": "discovery.target_career_stages",
    "eeo_race_ethnicities": "eeo.race_ethnicities",
}
NUMBER_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}


def fact_values(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Fact rows as `{key: value}`."""
    return {str(fact["key"]): fact.get("value") for fact in facts if fact.get("key")}


def _notice_text(weeks: Any) -> str | None:
    if isinstance(weeks, bool) or not isinstance(weeks, int | float):
        return None
    count = int(weeks) if float(weeks).is_integer() else weeks
    word = NUMBER_WORDS.get(count, str(count)) if isinstance(count, int) else str(count)
    unit = "week" if count == 1 else "weeks"
    return f"{word} {unit} from offer acceptance."


def legacy_profile(
    facts: list[dict[str, Any]], today: date, *, career: dict[str, Any] | None = None
) -> dict[str, Any]:
    # A stored fact shadows the career-derived value with the same key (R51).
    values = {**career_facts(career, today), **fact_values(facts)}
    derived = derive(values, today)
    profile: dict[str, Any] = {column: values.get(key) for column, key in DIRECT.items()}
    profile.update({column: list(values.get(key) or []) for column, key in LISTS.items()})

    citizen = values.get("work_auth.us_citizen") is True
    authorized = values.get("work_auth.authorized_us")
    profile["work_authorization_status"] = (
        "Yes" if citizen or authorized is True else "No" if authorized is False else None
    )
    profile["visa_status"] = "Citizen" if citizen else None

    profile["graduation_term"] = derived.get("education.graduation_term")
    profile["completed_coop_terms"] = derived.get("coop.completed_rotations")
    profile["earliest_start_text"] = _notice_text(values.get("availability.notice_period_weeks"))

    minimum = values.get("compensation.minimum")
    minimum = minimum if isinstance(minimum, dict) else {}
    profile["minimum_salary"] = minimum.get("amount")
    profile["minimum_salary_period"] = minimum.get("period")
    profile["professional_summary"] = None
    return profile
