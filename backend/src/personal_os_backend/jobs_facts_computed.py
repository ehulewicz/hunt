"""The facts the worker computes, mirrored so FastAPI can refuse to store them.

A computed fact comes from the career record, from other facts or the date, or
from the posting being applied to; a value written for one goes stale the
moment it is saved, so the API must not store or verify one. A workers test
keeps this list equal to the worker's declarations.
"""

from typing import Literal

Source = Literal["career", "derived", "posting"]

COMPUTED_FACTS: dict[str, tuple[Source, str, tuple[str, ...]]] = {
    # career
    "education.school_name": ("career", "School", ("career.education",)),
    "education.degree_type": ("career", "Degree type", ("career.education",)),
    "education.field_of_study": ("career", "Field of study", ("career.education",)),
    "education.graduation_date": ("career", "Graduation date", ("career.education",)),
    "education.gpa": ("career", "GPA", ("career.education",)),
    "education.coursework": ("career", "Coursework", ("career.education",)),
    "education.high_school_name": ("career", "High school", ("career.education",)),
    "education.high_school_city": ("career", "High school city", ("career.education",)),
    "education.high_school_graduation_year": (
        "career",
        "High school graduation year",
        ("career.education",),
    ),
    "experience.years_professional": (
        "career",
        "Whole years of professional experience",
        ("career.experience",),
    ),
    "coop.completed_rotations": (
        "career",
        "Completed co-op rotations as of today",
        ("career.experience", "career.education"),
    ),
    "experience.prior_internship": (
        "career",
        "Has completed an internship or co-op",
        ("career.experience", "career.education"),
    ),
    "employment.current_employer": ("career", "Current employer", ("career.experience",)),
    "employment.current_title": (
        "career",
        "Job title at the current employer",
        ("career.experience",),
    ),
    "employment.current_start_date": (
        "career",
        "When the current job started",
        ("career.experience",),
    ),
    "employment.organizations": (
        "career",
        "Every organization you worked or researched at",
        ("career.experience", "career.research"),
    ),
    "skills.all": ("career", "Every skill on the resume, in order", ("career.skills",)),
    "experience.years_full_time": (
        "career",
        "Whole years of full-time industry experience, not counting internships, "
        "co-ops, contract or part-time work",
        ("career.experience",),
    ),
    # derived
    "education.graduation_term": ("derived", "Graduation term", ("education.graduation_date",)),
    "education.currently_enrolled": (
        "derived",
        "Currently enrolled as a student",
        ("education.graduation_date",),
    ),
    "compensation.minimum_annual": (
        "derived",
        "Minimum annual compensation",
        ("compensation.minimum",),
    ),
    "identity.first_name": ("derived", "First name, from the legal name", ("identity.legal_name",)),
    "identity.preferred_is_legal": (
        "derived",
        "The preferred first name is the legal first name",
        ("identity.preferred_name", "identity.legal_name"),
    ),
    "identity.age": (
        "derived",
        "Age in whole years, from the date of birth",
        ("identity.birth_date",),
    ),
    "education.degree_and_field": (
        "derived",
        "Degree and field together, as one answer",
        ("education.degree_type", "education.field_of_study"),
    ),
    "identity.adult": ("derived", "At least 18 years old", ("identity.birth_date",)),
    "identity.last_name": ("derived", "Last name, from the legal name", ("identity.legal_name",)),
    "address.location": (
        "derived",
        "Current or permanent location as city, state, country",
        ("address.city", "address.state", "address.country"),
    ),
    "work_auth.citizenship_country": (
        "derived",
        "Country of citizenship",
        ("work_auth.us_citizen",),
    ),
    "work_auth.status": (
        "derived",
        "Immigration or work authorization status",
        ("work_auth.us_citizen",),
    ),
    "work_auth.sponsorship_type": (
        "derived",
        "The kind of sponsorship needed: Not Applicable when none is",
        ("work_auth.requires_sponsorship",),
    ),
    "address.city_state_zip": (
        "derived",
        "City, state and postal code as one line",
        ("address.city", "address.state", "address.postal_code"),
    ),
    "address.full": (
        "derived",
        "Street address, city, state and postal code as one line",
        ("address.line_1", "address.city", "address.state", "address.postal_code"),
    ),
    "eeo.lgbtq": ("derived", "Identifies as LGBTQ+", ("eeo.transgender", "eeo.sexual_orientation")),
    "work_auth.us_person": (
        "derived",
        "U.S. person under export rules: a citizen or lawful permanent resident",
        ("work_auth.us_citizen",),
    ),
    "work_auth.permanent_resident": (
        "derived",
        "U.S. lawful permanent resident (green card holder) who is not a citizen",
        ("work_auth.us_citizen",),
    ),
    "work_auth.refugee_or_asylee": (
        "derived",
        "Granted refugee or asylum status in the U.S.",
        ("work_auth.us_citizen",),
    ),
    "work_auth.visa_based": (
        "derived",
        "Work eligibility rests on a work visa",
        ("work_auth.us_citizen",),
    ),
    "work_auth.deemed_export_applies": (
        "derived",
        "The deemed export rule applies to you (not a U.S. person)",
        ("work_auth.us_citizen",),
    ),
    "education.degree_level": (
        "derived",
        "Level of the degree you are pursuing or hold",
        ("education.degree_type",),
    ),
    "education.program_level": (
        "derived",
        "Undergraduate or graduate program",
        ("education.degree_level",),
    ),
    "education.highest_completed": (
        "derived",
        "Highest level of education completed",
        ("education.degree_level", "education.currently_enrolled"),
    ),
    "availability.full_time_start_year": (
        "derived",
        "Year you could start full time after graduating",
        ("education.graduation_date",),
    ),
    # posting
    "posting.title": ("posting", "The posting's title", ("the posting's title",)),
    "employment.worked_for": (
        "posting",
        "Has worked for the company in this posting",
        ("employment.organizations",),
    ),
    "employment.applied_before": (
        "posting",
        "Has submitted an application to this posting's company before",
        ("your applications",),
    ),
    "availability.earliest_start_date": (
        "posting",
        "Two weeks from accepting, or the posting's term start if later",
        ("availability.notice_period_weeks", "the posting's title"),
    ),
    "availability.internship_dates": (
        "posting",
        "The twelve-week window from the earliest start, as one line",
        ("availability.earliest_start_date",),
    ),
    "availability.term": ("posting", "The term the posting names", ("the posting's title",)),
    "availability.conflicts_with_school": (
        "posting",
        "Whether the window runs into your last semester",
        ("availability.internship_dates", "education.graduation_date"),
    ),
    "education.enrolled_through_internship": (
        "posting",
        "Still enrolled in your degree program through this posting's internship",
        ("availability.internship_dates", "education.graduation_date"),
    ),
    "referral.referred": ("posting", "Referred for this application (apply --referred)", ()),
    "referral.referrer_name": ("posting", "Who referred you (apply --referred NAME)", ()),
}
