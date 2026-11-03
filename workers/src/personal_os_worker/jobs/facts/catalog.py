"""The fact catalog: every fact key the system knows how to use (design §7.2).

A key is `stored` (a row in `jobs_facts` that you set and verify), `career`
(derived from the career record), or `derived` (computed from other facts, the
date, or the posting). The archive's translations, the Inbox, and the facts
session all name facts by these keys, so a fact proposed from an old answer
arrives with the right label and type.

`fact_values` turns fact rows into a plain `{key: value}` mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Source = Literal["stored", "career", "derived"]
ValueType = Literal["text", "boolean", "number", "date", "choice", "list", "object"]


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    key: str
    label: str
    value_type: ValueType
    source: Source = "stored"


def _entries(*items: CatalogEntry) -> dict[str, CatalogEntry]:
    return {item.key: item for item in items}


CATALOG = _entries(
    CatalogEntry("identity.legal_name", "Full legal name", "text"),
    CatalogEntry("identity.preferred_name", "Preferred first name", "text"),
    CatalogEntry("identity.middle_name", "Middle name", "text"),
    CatalogEntry(
        "identity.preferred_is_legal",
        "The preferred first name is the legal first name",
        "boolean",
        "derived",
    ),
    CatalogEntry("identity.first_name", "First name, from the legal name", "text", "derived"),
    CatalogEntry("identity.last_name", "Last name, from the legal name", "text", "derived"),
    CatalogEntry("identity.birth_date", "Date of birth", "date"),
    CatalogEntry("identity.age", "Age in whole years, from the date of birth", "number", "derived"),
    CatalogEntry("identity.adult", "At least 18 years old", "boolean", "derived"),
    CatalogEntry("identity.pronouns", "Pronouns", "text"),
    CatalogEntry("contact.email", "Email address", "text"),
    CatalogEntry("contact.phone", "Phone number", "text"),
    CatalogEntry(
        "contact.phone_country",
        "The phone number's country and calling code, as {country, calling_code}",
        "object",
    ),
    CatalogEntry("contact.phone_extension", "Phone extension", "text"),
    CatalogEntry("contact.linkedin_url", "LinkedIn profile URL", "text"),
    CatalogEntry("contact.github_url", "GitHub profile URL", "text"),
    CatalogEntry("contact.portfolio_url", "Portfolio or personal website URL", "text"),
    CatalogEntry("contact.x_url", "X (Twitter) profile URL", "text"),
    CatalogEntry("contact.facebook_url", "Facebook profile URL", "text"),
    CatalogEntry("address.line_1", "Street address (current, permanent, and mailing)", "text"),
    CatalogEntry("address.line_2", "Second address line", "text"),
    CatalogEntry("address.city", "City (current, permanent, and mailing)", "text"),
    CatalogEntry("address.state", "State (current, permanent, and mailing)", "text"),
    CatalogEntry("address.postal_code", "Postal code", "text"),
    CatalogEntry("address.country", "Country of residence", "text"),
    CatalogEntry(
        "address.location", "Current or permanent location as city, state, country",
        "text", "derived"
    ),
    CatalogEntry(
        "address.city_state_zip", "City, state and postal code as one line", "text", "derived"
    ),
    CatalogEntry(
        "address.full", "Street address, city, state and postal code as one line", "text",
        "derived",
    ),
    CatalogEntry("education.school_name", "School", "text", "career"),
    CatalogEntry("education.degree_type", "Degree type", "text", "career"),
    CatalogEntry("education.field_of_study", "Field of study", "text", "career"),
    CatalogEntry("education.graduation_date", "Graduation date", "date", "career"),
    CatalogEntry(
        "education.degree_and_field", "Degree and field together, as one answer", "text",
        "derived",
    ),
    CatalogEntry("education.gpa", "GPA", "number", "career"),
    CatalogEntry("education.coursework", "Coursework", "list", "career"),
    CatalogEntry("education.high_school_name", "High school", "text", "career"),
    CatalogEntry("education.high_school_city", "High school city", "text", "career"),
    CatalogEntry(
        "education.high_school_graduation_year",
        "High school graduation year",
        "number",
        "career",
    ),
    CatalogEntry("education.certifications", "Certifications held", "list"),
    CatalogEntry("education.graduation_term", "Graduation term", "text", "derived"),
    CatalogEntry(
        "education.currently_enrolled", "Currently enrolled as a student", "boolean", "derived"
    ),
    CatalogEntry(
        "coop.completed_rotations", "Completed co-op rotations as of today", "number", "career"
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
        "work_auth.status", "Immigration or work authorization status", "text", "derived"
    ),
    CatalogEntry(
        "work_auth.sponsorship_type",
        "The kind of sponsorship needed: Not Applicable when none is",
        "text",
        "derived",
    ),
    CatalogEntry(
        "work_auth.sanctioned_country_citizen",
        "Citizen or resident of a sanctioned or export-controlled country",
        "boolean",
    ),
    CatalogEntry(
        "availability.earliest_start_date",
        "Two weeks from accepting, or the posting's term start if later",
        "date",
        "derived",
    ),
    CatalogEntry(
        "availability.internship_dates",
        "The twelve-week window from the earliest start, as one line",
        "text",
        "derived",
    ),
    CatalogEntry("availability.term", "The term the posting names", "text", "derived"),
    CatalogEntry(
        "availability.conflicts_with_school",
        "Whether the window runs into your last semester",
        "boolean",
        "derived",
    ),
    CatalogEntry("availability.remote", "Open to working remotely", "boolean"),
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
        "preferences.application_start",
        "Preferred way in when a board offers several (design §10.2)",
        "choice",
    ),
    CatalogEntry(
        "preferences.location_strategy", "Office selection policy (design §9)", "object"
    ),
    CatalogEntry(
        "preferences.engineering_focus",
        "Engineering interests, unordered: the posting decides which one, "
        "else one is drawn at random",
        "list",
    ),
    CatalogEntry(
        "preferences.application_source",
        "How you came to a posting, in a free-text box; menus rank the employer's site, "
        "a job board, then Other",
        "text",
    ),
    CatalogEntry(
        "consent.agrees", "Agree to consent, terms, privacy and attestation questions", "boolean"
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
        "experience.prior_internship", "Has completed an internship or co-op", "boolean", "career"
    ),
    CatalogEntry("employment.current_employer", "Current employer", "text", "career"),
    CatalogEntry("employment.current_title", "Job title at the current employer", "text", "career"),
    CatalogEntry(
        "employment.current_start_date", "When the current job started", "date", "career"
    ),
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
    CatalogEntry(
        "employment.applied_before",
        "Has submitted an application to this posting's company before",
        "boolean",
        "derived",
    ),
    CatalogEntry("posting.title", "The posting's title", "text", "derived"),
    CatalogEntry("skills.all", "Every skill on the resume, in order", "list", "career"),
    CatalogEntry(
        "referral.referred", "Referred for this application (apply --referred)", "boolean",
        "derived",
    ),
    CatalogEntry(
        "referral.referrer_name", "Who referred you (apply --referred NAME)", "text", "derived"
    ),
    CatalogEntry("work_auth.security_clearance", "Holds an active security clearance", "boolean"),
    CatalogEntry(
        "employment.government", "Has worked for the US government (prior employment)", "boolean"
    ),
    CatalogEntry(
        "employment.relatives_at_company",
        "Has relatives or close relationships at the posting's company",
        "boolean",
    ),
    CatalogEntry(
        "employment.restrictive_agreement",
        "Bound by a non-compete or non-disclosure agreement that could restrict a new job",
        "boolean",
    ),
    CatalogEntry("identity.other_languages", "Languages spoken besides English", "text"),
    CatalogEntry("education.class_standing", "Class standing (year in school)", "text"),
    CatalogEntry("education.start_date", "When you started your degree", "date"),
    CatalogEntry("preferences.position_type", "Internship or co-op preference", "text"),
    CatalogEntry("eeo.gender_identity", "Gender identity", "text"),
    CatalogEntry("eeo.transgender", "Transgender", "boolean"),
    CatalogEntry("eeo.race_ethnicities", "Race and ethnicity", "list"),
    CatalogEntry("eeo.hispanic_latino", "Hispanic or Latino", "boolean"),
    CatalogEntry("eeo.sexual_orientation", "Sexual orientation", "text"),
    CatalogEntry("eeo.veteran_status", "Veteran status", "text"),
    CatalogEntry("eeo.disability_status", "Disability status", "text"),
    CatalogEntry("discovery.target_roles", "Target roles for discovery", "list"),
    CatalogEntry("discovery.target_locations", "Target locations for discovery", "list"),
    CatalogEntry(
        "discovery.target_career_stages", "Target career stages for discovery", "list"
    ),
    CatalogEntry("eeo.lgbtq", "Identifies as LGBTQ+", "boolean", "derived"),
    CatalogEntry(
        "work_auth.us_person",
        "U.S. person under export rules: a citizen or lawful permanent resident",
        "boolean",
        "derived",
    ),
    CatalogEntry(
        "work_auth.permanent_resident",
        "U.S. lawful permanent resident (green card holder) who is not a citizen",
        "boolean",
        "derived",
    ),
    CatalogEntry(
        "work_auth.refugee_or_asylee",
        "Granted refugee or asylum status in the U.S.",
        "boolean",
        "derived",
    ),
    CatalogEntry(
        "work_auth.visa_based", "Work eligibility rests on a work visa", "boolean", "derived"
    ),
    CatalogEntry(
        "work_auth.deemed_export_applies",
        "The deemed export rule applies to you (not a U.S. person)",
        "boolean",
        "derived",
    ),
    CatalogEntry(
        "education.degree_level",
        "Level of the degree you are pursuing or hold",
        "text",
        "derived",
    ),
    CatalogEntry(
        "education.program_level", "Undergraduate or graduate program", "text", "derived"
    ),
    CatalogEntry(
        "education.highest_completed",
        "Highest level of education completed",
        "text",
        "derived",
    ),
    CatalogEntry(
        "availability.full_time_start_year",
        "Year you could start full time after graduating",
        "number",
        "derived",
    ),
    CatalogEntry(
        "education.enrolled_through_internship",
        "Still enrolled in your degree program through this posting's internship",
        "boolean",
        "derived",
    ),
    CatalogEntry(
        "experience.years_full_time",
        "Whole years of full-time industry experience, not counting internships, "
        "co-ops, contract or part-time work",
        "number",
        "career",
    ),
)


def entry(key: str) -> CatalogEntry | None:
    return CATALOG.get(key)


def fact_values(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Fact rows as `{key: value}`."""
    return {str(fact["key"]): fact.get("value") for fact in facts if fact.get("key")}
