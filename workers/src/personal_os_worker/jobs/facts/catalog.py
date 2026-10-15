"""The fact catalog: every fact key the system knows how to use (design §7.2).

A key is `stored` (a row in `jobs_facts` that you set and verify), `career`
(derived from the career record), or `derived` (computed from other facts, the
date, or the posting). The archive's translations, the Inbox, and the facts
session all name facts by these keys, so a fact proposed from an old answer
arrives with the right label, type, and sensitivity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Source = Literal["stored", "career", "derived"]
ValueType = Literal["text", "boolean", "number", "date", "choice", "list", "object"]


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    key: str
    label: str
    value_type: ValueType
    source: Source = "stored"
    sensitive: bool = False


def _entries(*items: CatalogEntry) -> dict[str, CatalogEntry]:
    return {item.key: item for item in items}


CATALOG = _entries(
    CatalogEntry("identity.legal_name", "Full legal name", "text"),
    CatalogEntry("identity.preferred_name", "Preferred first name", "text"),
    CatalogEntry("identity.first_name", "First name, from the legal name", "text", "derived"),
    CatalogEntry("identity.last_name", "Last name, from the legal name", "text", "derived"),
    CatalogEntry("identity.adult", "At least 18 years old", "boolean"),
    CatalogEntry("identity.pronouns", "Pronouns", "text"),
    CatalogEntry("contact.email", "Email address", "text"),
    CatalogEntry("contact.phone", "Phone number", "text", sensitive=True),
    CatalogEntry("contact.linkedin_url", "LinkedIn profile URL", "text"),
    CatalogEntry("contact.github_url", "GitHub profile URL", "text"),
    CatalogEntry("contact.portfolio_url", "Portfolio or personal website URL", "text"),
    CatalogEntry("address.line_1", "Street address", "text", sensitive=True),
    CatalogEntry("address.line_2", "Second address line", "text", sensitive=True),
    CatalogEntry("address.city", "Current city", "text"),
    CatalogEntry("address.state", "Current state", "text"),
    CatalogEntry("address.postal_code", "Postal code", "text", sensitive=True),
    CatalogEntry("address.country", "Country of residence", "text"),
    CatalogEntry(
        "address.location", "Current location as city, state, country", "text", "derived"
    ),
    CatalogEntry("education.school_name", "School", "text", "career"),
    CatalogEntry("education.degree_type", "Degree type", "text", "career"),
    CatalogEntry("education.field_of_study", "Field of study", "text", "career"),
    CatalogEntry("education.graduation_date", "Graduation date", "date", "career"),
    CatalogEntry("education.gpa", "GPA", "number", "career"),
    CatalogEntry("education.graduation_term", "Graduation term", "text", "derived"),
    CatalogEntry(
        "education.currently_enrolled", "Currently enrolled as a student", "boolean", "derived"
    ),
    CatalogEntry("coop.anchor", "Completed co-op rotations as of a date", "object"),
    CatalogEntry(
        "coop.completed_rotations", "Completed co-op rotations as of today", "number", "derived"
    ),
    CatalogEntry("work_auth.us_citizen", "United States citizen", "boolean"),
    CatalogEntry(
        "work_auth.authorized_us", "Authorized to work in the United States", "boolean"
    ),
    CatalogEntry(
        "work_auth.requires_sponsorship",
        "Requires visa sponsorship now or in the future",
        "boolean",
    ),
    CatalogEntry(
        "work_auth.citizenship_country", "Country of citizenship", "text", "derived"
    ),
    CatalogEntry(
        "work_auth.sanctioned_country_citizen",
        "Citizen or resident of a sanctioned or export-controlled country",
        "boolean",
    ),
    CatalogEntry("availability.earliest_start_date", "Earliest start date", "date"),
    CatalogEntry(
        "availability.notice_period_weeks", "Notice period before starting, in weeks", "number"
    ),
    CatalogEntry(
        "availability.onsite", "Available to work on-site or in person", "boolean"
    ),
    CatalogEntry("preferences.willing_to_relocate", "Willing to relocate", "boolean"),
    CatalogEntry("preferences.willing_to_travel", "Willing to travel", "boolean"),
    CatalogEntry("preferences.remote", "Remote work preference", "choice"),
    CatalogEntry(
        "preferences.location_strategy", "Office selection policy (design §9)", "object"
    ),
    CatalogEntry("compensation.minimum", "Minimum compensation", "object"),
    CatalogEntry(
        "compensation.minimum_annual", "Minimum annual compensation", "number", "derived"
    ),
    CatalogEntry(
        "compensation.accepts_stated_range",
        "Comfortable with a pay range the posting states",
        "boolean",
    ),
    CatalogEntry(
        "experience.years_professional",
        "Whole years of professional experience",
        "number",
        "career",
    ),
    CatalogEntry(
        "experience.prior_internship", "Has completed an internship or co-op", "boolean", "derived"
    ),
    CatalogEntry("employment.current_employer", "Current employer", "text", "career"),
    CatalogEntry(
        "employment.organizations", "Every organization you worked or researched at", "list",
        "career",
    ),
    CatalogEntry(
        "employment.worked_for",
        "Has worked for the company in this posting",
        "boolean",
        "derived",
    ),
    CatalogEntry("skills.all", "Every skill on the resume, in order", "list", "career"),
    CatalogEntry("referral.status", "How you were referred; none by default", "choice"),
    CatalogEntry("eeo.gender_identity", "Gender identity", "text", sensitive=True),
    CatalogEntry("eeo.transgender", "Transgender", "boolean", sensitive=True),
    CatalogEntry("eeo.race_ethnicities", "Race and ethnicity", "list", sensitive=True),
    CatalogEntry("eeo.hispanic_latino", "Hispanic or Latino", "boolean", sensitive=True),
    CatalogEntry("eeo.sexual_orientation", "Sexual orientation", "text", sensitive=True),
    CatalogEntry("eeo.veteran_status", "Veteran status", "text", sensitive=True),
    CatalogEntry("eeo.disability_status", "Disability status", "text", sensitive=True),
    CatalogEntry("discovery.target_roles", "Target roles for discovery", "list"),
    CatalogEntry("discovery.target_locations", "Target locations for discovery", "list"),
    CatalogEntry(
        "discovery.target_career_stages", "Target career stages for discovery", "list"
    ),
)


def entry(key: str) -> CatalogEntry | None:
    return CATALOG.get(key)


def settable(key: str) -> bool:
    """Whether a value for this key is stored by you rather than derived."""
    known = CATALOG.get(key)
    return known is None or known.source == "stored"
