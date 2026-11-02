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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from personal_os_worker.jobs.options import SYNONYM_GROUPS, normalize_answer_question
from personal_os_worker.jobs.sites import is_non_answer_option

# A co-op rotation is complete once its boundary is reached: spring on May 1,
# summer on August 1, fall on November 1 (decided 2026-10-08). Rotations are
# counted over each co-op entry's span on the career record (decided 2026-10-23).
ROTATION_BOUNDARIES = ((5, 1), (8, 1), (11, 1))
# Entries that count as having done an internship once they have ended.
INTERNSHIP_TYPES = frozenset({"internship", "coop"})
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
DAYS_PER_YEAR = 365.25


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


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


# When a term's internship usually starts (month, day), and how long one runs
# (decided 2026-10-18: aim for twelve-week ranges).
TERM_STARTS = {"winter": (1, 4), "spring": (1, 11), "summer": (5, 18), "fall": (8, 31)}
INTERNSHIP_WEEKS = 12
TERM = re.compile(
    r"\b(winter|spring|summer|fall|autumn)\b(?:\s*[/&-]\s*(?:winter|spring|summer|fall|autumn))*"
    r"\s*['\u2019]?(\d{4}|\d{2})\b",
    re.IGNORECASE,
)


def internship_window(
    title: str, today: date, notice_weeks: Any = 2
) -> tuple[date, date, str | None]:
    """(start, end, term) for this posting.

    You can start two weeks after accepting (your notice period); a posting
    that names its term ("Summer 2027") starts no earlier than that term does.
    The window runs twelve weeks from the start.
    """
    try:
        weeks = max(0, int(notice_weeks))
    except (TypeError, ValueError):
        weeks = 2
    earliest = today + timedelta(weeks=weeks)
    found = TERM.search(title or "")
    if found is None:
        return earliest, earliest + timedelta(weeks=INTERNSHIP_WEEKS), None
    season = "fall" if found.group(1).casefold() == "autumn" else found.group(1).casefold()
    year = int(found.group(2))
    year = year + 2000 if year < 100 else year
    month, day = TERM_STARTS[season]
    start = max(date(year, month, day), earliest)
    return start, start + timedelta(weeks=INTERNSHIP_WEEKS), f"{season.title()} {year}"


def overlaps_final_term(start: date, end: date, graduation: date | None) -> bool:
    """Whether the window runs into the last semester before graduating, which you
    attend (decided 2026-10-18: avoid Fall 2027)."""
    if graduation is None:
        return False
    final_term_start = date(graduation.year, 8, 15) if graduation.month >= 9 else date(
        graduation.year, 1, 5
    )
    return start < graduation and end > final_term_start


def _long_date(value: date) -> str:
    return f"{value:%B} {value.day}, {value.year}"


def posting_facts(
    facts: dict[str, Any],
    company: str,
    prior_applications: Iterable[dict[str, Any]] | None = None,
    *,
    title: str = "",
    today: date | None = None,
    referral: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Facts about you and this posting: its company, its term, how you came to it."""
    found: dict[str, Any] = {}
    day = today or date.today()
    if title.strip():
        found["posting.title"] = title.strip()
    start, end, term = internship_window(title, day, facts.get("availability.notice_period_weeks"))
    found["availability.earliest_start_date"] = start.isoformat()
    found["availability.internship_dates"] = f"{_long_date(start)} \u2013 {_long_date(end)}"
    found["availability.conflicts_with_school"] = overlaps_final_term(
        start, end, _date(facts.get("education.graduation_date"))
    )
    graduation = _date(facts.get("education.graduation_date"))
    if graduation is not None:
        found["education.enrolled_through_internship"] = graduation > end
    if term is not None:
        found["availability.term"] = term
    # A referral is about this application, not about you: `apply --referred`.
    referred = referral or {}
    found["referral.referred"] = bool(referred.get("referred"))
    if referred.get("name"):
        found["referral.referrer_name"] = str(referred["name"])
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


def completed_rotations(
    entries: list[dict[str, Any]], today: date, graduation: date | None = None
) -> int:
    """Rotation boundaries passed while a co-op entry ran, each counted once.

    A current entry runs to today; an ended one through the end of its last
    month, so a spring rotation ending in April completes on May 1. Nothing
    counts past graduation.
    """
    passed: set[date] = set()
    for entry in entries:
        if entry.get("section") != "experience" or entry.get("employment_type") != "coop":
            continue
        start = _date(entry.get("start_date"))
        if start is None:
            continue
        end = _date(entry.get("end_date"))
        until = min(_next_month(end), today) if end is not None else today
        if graduation is not None:
            until = min(until, graduation)
        passed.update(
            boundary
            for year in range(start.year, until.year + 1)
            for month, day in ROTATION_BOUNDARIES
            if start < (boundary := date(year, month, day)) <= until
        )
    return len(passed)


def prior_internship(entries: list[dict[str, Any]], today: date, rotations: int) -> bool:
    """An internship or co-op entry has ended, or a co-op has completed a rotation."""
    ended = any(
        entry.get("section") == "experience"
        and entry.get("employment_type") in INTERNSHIP_TYPES
        and (end := _date(entry.get("end_date"))) is not None
        and end <= today
        for entry in entries
    )
    return ended or rotations > 0


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
        values["education.coursework"] = [
            str(course).strip()
            for course in education.get("coursework") or []
            if str(course).strip()
        ] or None
    # The record's non-primary schools: a high school row feeds these.
    schools = [row for row in record.get("schools") or [] if isinstance(row, dict)]
    high_school = next((row for row in schools if row.get("level") == "high_school"), None)
    if high_school is not None:
        values["education.high_school_name"] = high_school.get("school")
        location = str(high_school.get("location") or "").strip()
        values["education.high_school_city"] = location.split(",", 1)[0].strip() or None
        graduation = _date(high_school.get("graduation_date"))
        if graduation is not None:
            values["education.high_school_graduation_year"] = graduation.year
        else:
            match = re.search(r"\b(\d{4})\b", str(high_school.get("graduation") or ""))
            values["education.high_school_graduation_year"] = (
                int(match.group(1)) if match else None
            )
    entries = [entry for entry in record.get("entries") or [] if isinstance(entry, dict)]
    values["experience.years_professional"] = years_of_experience(entries, today)
    values["experience.years_full_time"] = years_of_experience(
        [
            entry
            for entry in entries
            if entry.get("employment_type") in {None, "founder"}
        ],
        today,
    )
    rotations = completed_rotations(
        entries, today, _date(values.get("education.graduation_date"))
    )
    values["coop.completed_rotations"] = rotations
    values["experience.prior_internship"] = prior_internship(entries, today, rotations)
    current = next(
        (
            entry
            for entry in entries
            if entry.get("section") == "experience"
            and entry.get("start_date")
            and not entry.get("end_date")
            and (entry.get("employer") or entry.get("org"))
        ),
        None,
    )
    # A form gets an entry's `employer` and `job_title` where they are set, since a
    # heading need not be either; otherwise the heading's org and title.
    if current is not None:
        values["employment.current_employer"] = current.get("employer") or current.get("org")
        values["employment.current_title"] = current.get("job_title") or current.get("title")
        start = _date(current.get("start_date"))
        values["employment.current_start_date"] = start.isoformat() if start else None
    organizations: list[str] = []
    for entry in entries:
        # Where you worked or researched: a project's "org" is an award, not an
        # employer, and an activity is not employment.
        if entry.get("section") in ("project", "activity"):
            continue
        # A lab's name is its research heading; its org spells out the acronym.
        names = [entry.get("org"), entry.get("employer")]
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
