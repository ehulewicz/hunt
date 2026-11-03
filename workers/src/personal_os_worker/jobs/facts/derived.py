"""Derived facts: computed from base facts, the career record, and today's date.

A derived fact goes stale the moment it is written down, so nothing here is
persisted. Each one is a pure function of the base facts (keyed by their dotted
names), the career record's rows, and the date it is asked on.

Career-derived facts follow the resume: education from its primary row, tenure
from the union of experience spans, co-op rotations and prior internships from
its co-op and internship entries, the current employer, and every skill. Until
the facts session settles them, a stored fact with the same key wins (R51);
`shadowed` names the ones where the two disagree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from personal_os_worker.jobs.facts.career import (
    DAYS_PER_YEAR,
    INTERNSHIP_TYPES,
    ROTATION_BOUNDARIES,
    _date,
    _next_month,
    career_facts,
    completed_rotations,
    prior_internship,
    shadowed,
    years_of_experience,
)
from personal_os_worker.jobs.facts.posting import (
    INTERNSHIP_WEEKS,
    SUBMITTED_STATUSES,
    TERM,
    TERM_STARTS,
    _long_date,
    internship_window,
    overlaps_final_term,
    posting_facts,
    worked_for,
)
from personal_os_worker.jobs.options import SYNONYM_GROUPS, normalize_answer_question
from personal_os_worker.jobs.sites import is_non_answer_option

__all__ = [
    "CAREER_FACTS",
    "DAYS_PER_YEAR",
    "DERIVED_FACTS",
    "HOURS_PER_YEAR",
    "INTERNSHIP_TYPES",
    "INTERNSHIP_WEEKS",
    "POSTING_FACTS",
    "ROTATION_BOUNDARIES",
    "SUBMITTED_STATUSES",
    "TERM",
    "TERM_STARTS",
    "DerivedFact",
    "_date",
    "_long_date",
    "_next_month",
    "annual_minimum",
    "career_facts",
    "completed_rotations",
    "derive",
    "graduation_term",
    "internship_window",
    "overlaps_final_term",
    "posting_facts",
    "prior_internship",
    "shadowed",
    "worked_for",
    "years_of_experience",
]

HOURS_PER_YEAR = 2080


@dataclass(frozen=True, slots=True)
class DerivedFact:
    key: str
    label: str
    reads: tuple[str, ...]


DERIVED_FACTS = (
    DerivedFact(
        "education.graduation_term", "Graduation term", ("education.graduation_date",)
    ),
    DerivedFact(
        "education.currently_enrolled",
        "Currently enrolled as a student",
        ("education.graduation_date",),
    ),
    DerivedFact(
        "compensation.minimum_annual", "Minimum annual compensation", ("compensation.minimum",)
    ),
    DerivedFact("identity.first_name", "First name", ("identity.legal_name",)),
    DerivedFact(
        "identity.preferred_is_legal",
        "The preferred first name is the legal first name",
        ("identity.preferred_name", "identity.legal_name"),
    ),
    DerivedFact("identity.age", "Age in whole years", ("identity.birth_date",)),
    DerivedFact(
        "education.degree_and_field",
        "Degree and field together, as one answer",
        ("education.degree_type", "education.field_of_study"),
    ),
    DerivedFact("identity.adult", "At least 18 years old", ("identity.birth_date",)),
    DerivedFact("identity.last_name", "Last name", ("identity.legal_name",)),
    DerivedFact(
        "address.location",
        "Current or permanent location as city, state, country",
        ("address.city", "address.state", "address.country"),
    ),
    DerivedFact(
        "work_auth.citizenship_country", "Country of citizenship", ("work_auth.us_citizen",)
    ),
    DerivedFact(
        "work_auth.status", "Immigration or work authorization status", ("work_auth.us_citizen",)
    ),
    DerivedFact(
        "work_auth.sponsorship_type",
        "The kind of sponsorship needed: Not Applicable when none is",
        ("work_auth.requires_sponsorship",),
    ),
    DerivedFact(
        "address.city_state_zip",
        "City, state and postal code as one line",
        ("address.city", "address.state", "address.postal_code"),
    ),
    DerivedFact(
        "address.full",
        "Street address, city, state and postal code as one line",
        ("address.line_1", "address.city", "address.state", "address.postal_code"),
    ),
    DerivedFact(
        "eeo.lgbtq",
        "Identifies as LGBTQ+",
        ("eeo.transgender", "eeo.sexual_orientation"),
    ),
    DerivedFact(
        "work_auth.us_person",
        "U.S. person under export rules: a citizen or lawful permanent resident",
        ("work_auth.us_citizen",),
    ),
    DerivedFact(
        "work_auth.permanent_resident",
        "U.S. lawful permanent resident (green card holder) who is not a citizen",
        ("work_auth.us_citizen",),
    ),
    DerivedFact(
        "work_auth.refugee_or_asylee",
        "Granted refugee or asylum status in the U.S.",
        ("work_auth.us_citizen",),
    ),
    DerivedFact(
        "work_auth.visa_based",
        "Work eligibility rests on a work visa",
        ("work_auth.us_citizen",),
    ),
    DerivedFact(
        "work_auth.deemed_export_applies",
        "The deemed export rule applies to you (not a U.S. person)",
        ("work_auth.us_citizen",),
    ),
    DerivedFact(
        "education.degree_level",
        "Level of the degree you are pursuing or hold",
        ("education.degree_type",),
    ),
    DerivedFact(
        "education.program_level",
        "Undergraduate or graduate program",
        ("education.degree_level",),
    ),
    DerivedFact(
        "education.highest_completed",
        "Highest level of education completed",
        ("education.degree_level", "education.currently_enrolled"),
    ),
    DerivedFact(
        "availability.full_time_start_year",
        "Year you could start full time after graduating",
        ("education.graduation_date",),
    ),
)
# Derived from the posting being filled, so only known during an application.
POSTING_FACTS = (
    DerivedFact("posting.title", "The posting's title", ("the posting's title",)),
    DerivedFact(
        "employment.worked_for",
        "Has worked or researched at the company in this posting",
        ("employment.organizations",),
    ),
    DerivedFact(
        "employment.applied_before",
        "Has submitted an application to this posting's company before",
        ("your applications",),
    ),
    DerivedFact(
        "availability.earliest_start_date",
        "Two weeks from accepting, or the posting's term start if later",
        ("availability.notice_period_weeks", "the posting's title"),
    ),
    DerivedFact(
        "availability.internship_dates",
        "The twelve-week window from the earliest start, as one line",
        ("availability.earliest_start_date",),
    ),
    DerivedFact("availability.term", "The term the posting names", ("the posting's title",)),
    DerivedFact(
        "availability.conflicts_with_school",
        "Whether the window runs into your last semester",
        ("availability.internship_dates", "education.graduation_date"),
    ),
    DerivedFact(
        "education.enrolled_through_internship",
        "Still enrolled in your degree program through this posting's internship",
        ("availability.internship_dates", "education.graduation_date"),
    ),
    DerivedFact("referral.referred", "Referred for this application (apply --referred)", ()),
    DerivedFact("referral.referrer_name", "Who referred you (apply --referred NAME)", ()),
)


CAREER_FACTS = (
    DerivedFact("education.school_name", "School", ("career.education",)),
    DerivedFact("education.degree_type", "Degree type", ("career.education",)),
    DerivedFact("education.field_of_study", "Field of study", ("career.education",)),
    DerivedFact("education.graduation_date", "Graduation date", ("career.education",)),
    DerivedFact("education.gpa", "GPA", ("career.education",)),
    DerivedFact("education.coursework", "Coursework", ("career.education",)),
    DerivedFact("education.high_school_name", "High school", ("career.education",)),
    DerivedFact("education.high_school_city", "High school city", ("career.education",)),
    DerivedFact(
        "education.high_school_graduation_year",
        "High school graduation year",
        ("career.education",),
    ),
    DerivedFact(
        "experience.years_professional",
        "Whole years of professional experience (union of experience spans)",
        ("career.experience",),
    ),
    DerivedFact(
        "coop.completed_rotations",
        "Completed co-op rotations as of today (boundaries passed in co-op entries)",
        ("career.experience", "career.education"),
    ),
    DerivedFact(
        "experience.prior_internship",
        "Has completed an internship or co-op (an ended one, or a completed rotation)",
        ("career.experience", "career.education"),
    ),
    DerivedFact("employment.current_employer", "Current employer", ("career.experience",)),
    DerivedFact(
        "employment.current_title", "Job title at the current employer", ("career.experience",)
    ),
    DerivedFact(
        "employment.current_start_date", "When the current job started", ("career.experience",)
    ),
    DerivedFact(
        "employment.organizations",
        "Every organization you worked or researched at, labs included",
        ("career.experience", "career.research"),
    ),
    DerivedFact("skills.all", "Every skill on the resume, in order", ("career.skills",)),
    DerivedFact(
        "experience.years_full_time",
        "Whole years of full-time industry experience, not counting internships, "
        "co-ops, contract or part-time work",
        ("career.experience",),
    ),
)


def graduation_term(graduation: date) -> str:
    """The academic term a graduation date ends: December is Fall."""
    if graduation.month <= 5:
        return "Spring"
    if graduation.month <= 8:
        return "Summer"
    return "Fall"


def annual_minimum(minimum: Any) -> int | float | None:
    if not isinstance(minimum, dict):
        return None
    amount = minimum.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int | float):
        return None
    if minimum.get("period") == "hour":
        return amount * HOURS_PER_YEAR
    if minimum.get("period") == "year":
        return amount
    return None


_DEGREE_LEVELS = (
    (re.compile(r"\b(ph\s?d|doctor\w*)\b", re.IGNORECASE), "Doctorate"),
    (re.compile(r"\b(master|m\.?s\.?|m\.?a\.?|m\.?eng\.?|ms|ma)\b", re.IGNORECASE),
     "Master's degree"),
    (re.compile(r"\bassociate\w*\b", re.IGNORECASE), "Associate degree"),
    (
        re.compile(r"\b(bachelor\w*|b\.?s\.?|b\.?a\.?|bsc|bs|ba)\b", re.IGNORECASE),
        "Bachelor's degree",
    ),
)


def _degree_level(value: Any) -> str | None:
    """"Bachelor's degree", "Master's degree", "Doctorate", "Associate degree"
    for a degree type ("BS", "MEng"), or None for one it does not know."""
    text = str(value or "").strip()
    if not text:
        return None
    for pattern, level in _DEGREE_LEVELS:
        if pattern.search(text):
            return level
    return None


def _program_level(level: str | None) -> str | None:
    """"Undergraduate" or "Graduate" for a degree level."""
    if level in {"Bachelor's degree", "Associate degree"}:
        return "Undergraduate"
    if level in {"Master's degree", "Doctorate"}:
        return "Graduate"
    return None


def _highest_completed(level: str | None, enrolled: bool | None) -> str | None:
    """The degree already held: one rung below while still enrolled, the level
    itself once graduated. A doctorate in progress names no level."""
    if enrolled is not True:
        return level
    if level in {"Bachelor's degree", "Associate degree"}:
        return "High school"
    if level == "Master's degree":
        return "Bachelor's degree"
    return None


_STRAIGHT = next(group for group in SYNONYM_GROUPS if "straight" in group)


def _lgbtq(facts: dict[str, Any]) -> bool | None:
    """Whether the facts say LGBTQ+: transgender, or a sexual orientation that
    is not straight. Absent for a missing or declined orientation."""
    if facts.get("eeo.transgender") is True:
        return True
    raw = str(facts.get("eeo.sexual_orientation") or "").strip()
    orientation = normalize_answer_question(raw)
    if orientation and not is_non_answer_option(raw) and orientation not in _STRAIGHT:
        return True
    if facts.get("eeo.transgender") is False and orientation in _STRAIGHT:
        return False
    return None


def derive(facts: dict[str, Any], today: date) -> dict[str, Any]:
    """Every derived fact whose inputs are present, keyed by its dotted name."""
    graduation = _date(facts.get("education.graduation_date"))
    city, state, postal, line_1 = (
        str(facts.get(key) or "").strip()
        for key in ("address.city", "address.state", "address.postal_code", "address.line_1")
    )
    city_state_zip = f"{city}, {state} {postal}" if city and state and postal else None
    born = _date(facts.get("identity.birth_date"))
    age = (
        today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        if born
        else None
    )
    names = str(facts.get("identity.legal_name") or "").split()
    preferred = str(facts.get("identity.preferred_name") or "").strip()
    degree = _degree_level(facts.get("education.degree_type"))
    enrolled = today < graduation if graduation else None
    citizen = facts.get("work_auth.us_citizen") is True
    place = [
        str(facts.get(key) or "").strip()
        for key in ("address.city", "address.state", "address.country")
    ]
    values: dict[str, Any] = {
        "education.graduation_term": graduation_term(graduation) if graduation else None,
        "education.currently_enrolled": enrolled,
        "education.degree_level": degree,
        "education.program_level": _program_level(degree),
        "education.highest_completed": _highest_completed(degree, enrolled),
        "availability.full_time_start_year": (
            graduation.year + (graduation.month >= 11) if graduation else None
        ),
        "eeo.lgbtq": _lgbtq(facts),
        "work_auth.us_person": True if citizen else None,
        "work_auth.permanent_resident": False if citizen else None,
        "work_auth.refugee_or_asylee": False if citizen else None,
        "work_auth.visa_based": False if citizen else None,
        "work_auth.deemed_export_applies": False if citizen else None,
        "compensation.minimum_annual": annual_minimum(facts.get("compensation.minimum")),
        "identity.first_name": names[0] if names else None,
        "identity.preferred_is_legal": preferred.casefold() == names[0].casefold()
        if preferred and names
        else None,
        "identity.age": age,
        "education.degree_and_field": f"{facts['education.degree_type']} in "
        f"{facts['education.field_of_study']}"
        if facts.get("education.degree_type") and facts.get("education.field_of_study")
        else None,
        "identity.adult": age >= 18 if age is not None else None,
        "identity.last_name": names[-1] if len(names) > 1 else None,
        "address.location": ", ".join(part for part in place if part) or None,
        "work_auth.citizenship_country": "United States"
        if facts.get("work_auth.us_citizen") is True
        else None,
        "work_auth.status": "US Citizen" if facts.get("work_auth.us_citizen") is True else None,
        "work_auth.sponsorship_type": "Not Applicable"
        if facts.get("work_auth.requires_sponsorship") is False
        else None,
        "address.city_state_zip": city_state_zip,
        "address.full": f"{line_1}, {city_state_zip}" if line_1 and city_state_zip else None,
    }
    return {key: value for key, value in values.items() if value is not None}
