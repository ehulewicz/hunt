"""The worker's read-only facts, mirrored so FastAPI can refuse to store them.

A read-only fact is a record fact, a static value the career record holds, or
a computed fact, calculated from the career record, from other facts or the
date, or from the posting being applied to. The API must not store or verify
either: a record fact is edited in the career record, and a value written for
a computed one goes stale the moment it is saved. A workers test loads this
module by path and keeps its declarations and `read_only_refusal` equal to the
worker's, so it stays standard-library only.
"""

import re
from typing import Literal

Source = Literal["career", "derived", "posting"]
Kind = Literal["static", "computed"]

READ_ONLY_FACTS: dict[str, tuple[Source, Kind, str, tuple[str, ...]]] = {
    # career
    "education.school_name": ("career", "static", "School", ("career.education",)),
    "education.degree_type": ("career", "static", "Degree type", ("career.education",)),
    "education.field_of_study": ("career", "static", "Field of study", ("career.education",)),
    "education.graduation_date": ("career", "static", "Graduation date", ("career.education",)),
    "education.gpa": ("career", "static", "GPA", ("career.education",)),
    "education.coursework": ("career", "static", "Coursework", ("career.education",)),
    "education.high_school_name": ("career", "static", "High school", ("career.education",)),
    "education.high_school_city": ("career", "static", "High school city", ("career.education",)),
    "education.high_school_graduation_year": (
        "career",
        "static",
        "High school graduation year",
        ("career.education",),
    ),
    "experience.years_professional": (
        "career",
        "computed",
        "Years of professional experience, also the answer for full-time, work or industry "
        "experience: experience and research entries, overlaps counted once, in half years (two "
        "months past a half year rounds up)",
        ("career.experience", "career.research"),
    ),
    "coop.completed_rotations": (
        "career",
        "computed",
        "Completed co-op rotations as of today",
        ("career.experience", "career.education"),
    ),
    "experience.prior_internship": (
        "career",
        "computed",
        "Has completed an internship or co-op",
        ("career.experience", "career.education"),
    ),
    "employment.current_employer": ("career", "static", "Current employer", ("career.experience",)),
    "employment.current_title": (
        "career",
        "static",
        "Job title at the current employer",
        ("career.experience",),
    ),
    "employment.current_start_date": (
        "career",
        "static",
        "When the current job started",
        ("career.experience",),
    ),
    "employment.organizations": (
        "career",
        "static",
        "Every organization you worked or researched at",
        ("career.experience", "career.research"),
    ),
    "skills.all": ("career", "static", "Every skill on the resume, in order", ("career.skills",)),
    "experience.robotics_competitions": (
        "career",
        "computed",
        "Competition robotics teams, years and roles",
        ("career.activities",),
    ),
    # derived
    "education.graduation_term": (
        "derived",
        "computed",
        "Graduation term",
        ("education.graduation_date",),
    ),
    "education.currently_enrolled": (
        "derived",
        "computed",
        "Currently enrolled as a student",
        ("education.graduation_date",),
    ),
    "compensation.minimum_annual": (
        "derived",
        "computed",
        "Minimum annual compensation",
        ("compensation.minimum",),
    ),
    "identity.first_name": (
        "derived",
        "computed",
        "First name, from the legal name",
        ("identity.legal_name",),
    ),
    "identity.preferred_is_legal": (
        "derived",
        "computed",
        "The preferred first name is the legal first name",
        ("identity.preferred_name", "identity.legal_name"),
    ),
    "identity.age": (
        "derived",
        "computed",
        "Age in whole years, from the date of birth",
        ("identity.birth_date",),
    ),
    "education.degree_and_field": (
        "derived",
        "computed",
        "Degree and field together, as one answer",
        ("education.degree_type", "education.field_of_study"),
    ),
    "identity.adult": ("derived", "computed", "At least 18 years old", ("identity.birth_date",)),
    "identity.last_name": (
        "derived",
        "computed",
        "Last name, from the legal name",
        ("identity.legal_name",),
    ),
    "address.location": (
        "derived",
        "computed",
        "Current or permanent location as city, state, country",
        ("address.city", "address.state", "address.country"),
    ),
    "work_auth.citizenship_country": (
        "derived",
        "computed",
        "Country of citizenship",
        ("work_auth.us_citizen",),
    ),
    "work_auth.status": (
        "derived",
        "computed",
        "Immigration or work authorization status",
        ("work_auth.us_citizen",),
    ),
    "work_auth.sponsorship_type": (
        "derived",
        "computed",
        "The kind of sponsorship needed: Not Applicable when none is",
        ("work_auth.requires_sponsorship",),
    ),
    "address.city_state_zip": (
        "derived",
        "computed",
        "City, state and postal code as one line",
        ("address.city", "address.state", "address.postal_code"),
    ),
    "address.full": (
        "derived",
        "computed",
        "Street address, city, state and postal code as one line",
        ("address.line_1", "address.city", "address.state", "address.postal_code"),
    ),
    "eeo.lgbtq": (
        "derived",
        "computed",
        "Identifies as LGBTQ+",
        ("eeo.transgender", "eeo.sexual_orientation"),
    ),
    "work_auth.us_person": (
        "derived",
        "computed",
        "U.S. person under export rules: a citizen or lawful permanent resident",
        ("work_auth.us_citizen",),
    ),
    "work_auth.permanent_resident": (
        "derived",
        "computed",
        "U.S. lawful permanent resident (green card holder) who is not a citizen",
        ("work_auth.us_citizen",),
    ),
    "work_auth.refugee_or_asylee": (
        "derived",
        "computed",
        "Granted refugee or asylum status in the U.S.",
        ("work_auth.us_citizen",),
    ),
    "work_auth.visa_based": (
        "derived",
        "computed",
        "Work eligibility rests on a work visa",
        ("work_auth.us_citizen",),
    ),
    "work_auth.deemed_export_applies": (
        "derived",
        "computed",
        "The deemed export rule applies to you (not a U.S. person)",
        ("work_auth.us_citizen",),
    ),
    "education.degree_level": (
        "derived",
        "computed",
        "Level of the degree you are pursuing or hold",
        ("education.degree_type",),
    ),
    "education.program_level": (
        "derived",
        "computed",
        "Undergraduate or graduate program",
        ("education.degree_level",),
    ),
    "education.highest_completed": (
        "derived",
        "computed",
        "Highest level of education completed",
        ("education.degree_level", "education.currently_enrolled"),
    ),
    "availability.full_time_start_year": (
        "derived",
        "computed",
        "Year you could start full time after graduating",
        ("education.graduation_date",),
    ),
    # posting
    "posting.title": ("posting", "computed", "The posting's title", ("the posting's title",)),
    "employment.worked_for": (
        "posting",
        "computed",
        "Has worked for the company in this posting",
        ("employment.organizations",),
    ),
    "employment.applied_before": (
        "posting",
        "computed",
        "Has submitted an application to this posting's company before",
        ("your applications",),
    ),
    "availability.earliest_start_date": (
        "posting",
        "computed",
        "Two weeks from accepting, or the posting's term start if later",
        ("availability.notice_period_weeks", "the posting's title"),
    ),
    "availability.internship_dates": (
        "posting",
        "computed",
        "The twelve-week window from the earliest start, as one line",
        ("availability.earliest_start_date",),
    ),
    "availability.term": (
        "posting",
        "computed",
        "The term the posting names",
        ("the posting's title",),
    ),
    "availability.conflicts_with_school": (
        "posting",
        "computed",
        "Whether the window runs into your last semester",
        ("availability.internship_dates", "education.graduation_date"),
    ),
    "education.enrolled_through_internship": (
        "posting",
        "computed",
        "Still enrolled in your degree program through this posting's internship",
        ("availability.internship_dates", "education.graduation_date"),
    ),
    "referral.referred": (
        "posting",
        "computed",
        "Referred for this application (apply --referred)",
        (),
    ),
    "referral.referrer_name": (
        "posting",
        "computed",
        "Who referred you (apply --referred NAME)",
        (),
    ),
}

_FACT_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def read_only_refusal(key: str) -> str | None:
    """Why a read-only fact is never stored: a record fact is edited in the
    career record, and a computed one is decided by the career record, other
    facts, or the posting, so storing it would freeze it. A referrer named
    once would follow every application after. None for a key no declaration
    here owns.
    """
    found = READ_ONLY_FACTS.get(key)
    if found is None:
        return None
    source, kind, _label, reads = found
    if source == "career":
        how = "comes from" if kind == "static" else "is computed from"
        return (
            f"{key} {how} the career record; edit the record "
            "at /resume/edit or with os hunt resume put"
        )
    if key.startswith("referral."):
        return f"{key} is set for one application with os hunt apply --referred NAME"
    career: list[str] = []
    stored: list[str] = []
    seen = {key}
    queue = list(reads)
    position = 0
    while position < len(queue):
        read = queue[position]
        position += 1
        if read in seen:
            continue
        seen.add(read)
        known = READ_ONLY_FACTS.get(read)
        if known is None:
            if _FACT_KEY.fullmatch(read):
                stored.append(read)
        elif known[0] == "career":
            career.append(read)
        else:
            queue.extend(known[3])
    steps: list[str] = []
    if career:
        steps.append(f"edit {', '.join(career)} in the career record at /resume/edit")
    if stored:
        steps.append(f"set {', '.join(stored)}")
    tail = "; ".join(steps) if steps else "it is decided for each application"
    return f"{key} is computed from {', '.join(reads)}; {tail}"
