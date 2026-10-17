"""Derived facts: computed from base facts, the career record, and today's date.

A derived fact goes stale the moment it is written down, so nothing here is
persisted. Each one is a pure function of the base facts (keyed by their dotted
names), the career record's rows, and the date it is asked on.

Career-derived facts follow the resume: education from its primary row, tenure
from the union of experience spans, the current employer, and every skill. Until
the facts session settles them, a stored fact with the same key wins (R51);
`shadowed` names the ones where the two disagree.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

# A co-op rotation is complete once its boundary is reached: spring on May 1,
# summer on August 1, fall on November 1 (decided 2026-10-08).
ROTATION_BOUNDARIES = ((5, 1), (8, 1), (11, 1))
HOURS_PER_YEAR = 2080


@dataclass(frozen=True, slots=True)
class DerivedFact:
    key: str
    label: str
    reads: tuple[str, ...]


DERIVED_FACTS = (
    DerivedFact(
        "coop.completed_rotations",
        "Completed co-op rotations as of today",
        ("coop.anchor", "education.graduation_date"),
    ),
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
    DerivedFact(
        "experience.prior_internship",
        "Has completed an internship or co-op",
        ("coop.anchor", "education.graduation_date"),
    ),
    DerivedFact("identity.first_name", "First name", ("identity.legal_name",)),
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
        "Current location as city, state, country",
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
)
# Derived from the posting being filled, so only known during an application.
POSTING_FACTS = (
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
)


CAREER_FACTS = (
    DerivedFact("education.school_name", "School", ("career.education",)),
    DerivedFact("education.degree_type", "Degree type", ("career.education",)),
    DerivedFact("education.field_of_study", "Field of study", ("career.education",)),
    DerivedFact("education.graduation_date", "Graduation date", ("career.education",)),
    DerivedFact("education.gpa", "GPA", ("career.education",)),
    DerivedFact(
        "experience.years_professional",
        "Whole years of professional experience (union of experience spans)",
        ("career.experience",),
    ),
    DerivedFact("employment.current_employer", "Current employer", ("career.experience",)),
    DerivedFact(
        "employment.organizations",
        "Every organization you worked or researched at, labs included",
        ("career.experience", "career.research"),
    ),
    DerivedFact("skills.all", "Every skill on the resume, in order", ("career.skills",)),
)
DAYS_PER_YEAR = 365.25


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def completed_rotations(anchor: Any, graduation: date | None, today: date) -> int | None:
    """The anchor's count plus every boundary after it, up to today or graduation."""
    if not isinstance(anchor, dict):
        return None
    count = anchor.get("count")
    as_of = _date(anchor.get("as_of"))
    if isinstance(count, bool) or not isinstance(count, int) or as_of is None:
        return None
    until = min(today, graduation) if graduation is not None else today
    passed = sum(
        1
        for year in range(as_of.year, until.year + 1)
        for month, day in ROTATION_BOUNDARIES
        if as_of < date(year, month, day) <= until
    )
    return count + passed


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


def derive(facts: dict[str, Any], today: date) -> dict[str, Any]:
    """Every derived fact whose inputs are present, keyed by its dotted name."""
    graduation = _date(facts.get("education.graduation_date"))
    rotations = completed_rotations(facts.get("coop.anchor"), graduation, today)
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
    place = [
        str(facts.get(key) or "").strip()
        for key in ("address.city", "address.state", "address.country")
    ]
    values: dict[str, Any] = {
        "coop.completed_rotations": rotations,
        "education.graduation_term": graduation_term(graduation) if graduation else None,
        "education.currently_enrolled": today < graduation if graduation else None,
        "compensation.minimum_annual": annual_minimum(facts.get("compensation.minimum")),
        "experience.prior_internship": rotations > 0 if rotations is not None else None,
        "identity.first_name": names[0] if names else None,
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


def worked_for(organizations: Any, company: str) -> bool | None:
    """Whether the posting's company is one you worked or researched at.

    Matches either way round as whole words ("Example University" in "Example Lab @
    Example University"), ignoring case; None when there is nothing to compare.
    """
    needle = " ".join(company.casefold().split())
    if len(needle) < 3 or not isinstance(organizations, list) or not organizations:
        return None
    names = [" ".join(str(name).casefold().split()) for name in organizations]

    def contains(outer: str, inner: str) -> bool:
        return len(inner) >= 3 and re.search(rf"(?<!\w){re.escape(inner)}(?!\w)", outer) is not None

    return any(contains(name, needle) or contains(needle, name) for name in names if name)


# An application that went in, whatever happened after.
SUBMITTED_STATUSES = frozenset({"applied", "oa", "oa_completed", "interview", "offer", "rejected"})


def posting_facts(
    facts: dict[str, Any],
    company: str,
    prior_applications: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Facts about you and this posting's company: worked there, applied there before."""
    found: dict[str, Any] = {}
    known = worked_for(facts.get("employment.organizations"), company)
    if known is not None:
        found["employment.worked_for"] = known
    if prior_applications is not None:
        found["employment.applied_before"] = any(
            str(item.get("status")) in SUBMITTED_STATUSES for item in prior_applications
        )
    return found


def _next_month(value: date) -> date:
    return date(value.year + value.month // 12, value.month % 12 + 1, 1)


def years_of_experience(entries: list[dict[str, Any]], today: date) -> int | None:
    """Whole years covered by any experience entry, overlaps counted once.

    An entry runs from its first month through the end of its last month, or
    through today when it has no end. Research and projects never count.
    """
    spans: list[tuple[date, date]] = []
    for entry in entries:
        if entry.get("section") != "experience":
            continue
        start = _date(entry.get("start_date"))
        if start is None:
            continue
        end = _date(entry.get("end_date"))
        stop = _next_month(end) if end is not None else today
        if stop > start:
            spans.append((start, stop))
    if not spans:
        return None
    covered = 0
    current_start, current_stop = None, None
    for start, stop in sorted(spans):
        if current_stop is None or start > current_stop:
            if current_start is not None and current_stop is not None:
                covered += (current_stop - current_start).days
            current_start, current_stop = start, stop
        else:
            current_stop = max(current_stop, stop)
    if current_start is not None and current_stop is not None:
        covered += (current_stop - current_start).days
    return int(covered // DAYS_PER_YEAR)


def career_facts(record: dict[str, Any] | None, today: date) -> dict[str, Any]:
    """The facts the career record determines, keyed like stored facts."""
    if not isinstance(record, dict):
        return {}
    values: dict[str, Any] = {}
    education = record.get("education")
    if isinstance(education, dict):
        values["education.school_name"] = education.get("school")
        values["education.degree_type"] = education.get("degree_type")
        values["education.field_of_study"] = education.get("field_of_study")
        graduation = _date(education.get("graduation_date"))
        values["education.graduation_date"] = graduation.isoformat() if graduation else None
        try:
            values["education.gpa"] = (
                float(education["gpa"]) if education.get("gpa") is not None else None
            )
        except (TypeError, ValueError):
            values["education.gpa"] = None
    entries = [entry for entry in record.get("entries") or [] if isinstance(entry, dict)]
    values["experience.years_professional"] = years_of_experience(entries, today)
    current = next(
        (
            entry
            for entry in entries
            if entry.get("section") == "experience"
            and entry.get("start_date")
            and not entry.get("end_date")
            and entry.get("org")
        ),
        None,
    )
    values["employment.current_employer"] = current.get("org") if current else None
    organizations: list[str] = []
    for entry in entries:
        # Where you worked or researched: a project's "org" is an award, not an employer.
        if entry.get("section") == "project":
            continue
        # A lab's name is its research heading; its org spells out the acronym.
        names = [entry.get("org")]
        if entry.get("section") == "research":
            names.append(entry.get("title"))
        for name in names:
            text = str(name or "").strip()
            if text and text not in organizations:
                organizations.append(text)
    values["employment.organizations"] = organizations or None
    skills: list[str] = []
    for group in record.get("skills") or []:
        items = group.get("items") if isinstance(group, dict) else None
        for item in items or []:
            name = str(item).strip()
            if name and name not in skills:
                skills.append(name)
    values["skills.all"] = skills or None
    return {key: value for key, value in values.items() if value not in (None, "")}


def shadowed(stored: dict[str, Any], career: dict[str, Any]) -> dict[str, Any]:
    """Career-derived values that a stored fact with the same key currently overrides."""
    return {
        key: value for key, value in career.items() if key in stored and stored[key] != value
    }
