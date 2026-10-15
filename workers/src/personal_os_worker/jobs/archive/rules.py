"""The retired rules, kept as reference and translated to the facts they read.

Before the 2026-10-14 restart (design §0) these answered questions directly:
the exact-label map, the priority policies, the two library regex rows, the 26
builtin rules, and the worked-before rule. None of them fills anything any
more. Each keeps its old pattern, verbatim, and names the fact key it read —
its translation — so that when the new system meets a question the old one
knew, the archive can say which fact it asks about (R41). The value always comes
from that fact, never from the rule.

Tiers are tried in the old resolution order; the first tier with a match decides,
and within a tier exactly one translated fact must match, the same discipline
the old dispatch had.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

Tier = Literal["label", "priority", "library", "builtin", "worked_before"]
TIERS: tuple[Tier, ...] = ("label", "priority", "library", "builtin", "worked_before")
Condition = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class ArchivedRule:
    name: str
    tier: Tier
    pattern: re.Pattern[str]
    fact_key: str
    formatter: str | None = None
    note: str = ""
    # A further test on the field (its options, its section) the old rule made.
    when: Condition | None = None

    @property
    def ref(self) -> str:
        """The rule's stable name in the archive, recorded on what it proposes."""
        return f"{self.tier}:{self.name}"


def _rule(
    tier: Tier,
    name: str,
    pattern: str,
    fact_key: str,
    *,
    formatter: str | None = None,
    note: str = "",
    when: Condition | None = None,
) -> ArchivedRule:
    return ArchivedRule(
        name, tier, re.compile(pattern, re.IGNORECASE | re.DOTALL), fact_key, formatter, note, when
    )


def _options(field: dict[str, Any]) -> list[str]:
    return [str(option) for option in field.get("options") or []]


def _offers_citizenship_option(field: dict[str, Any]) -> bool:
    return any(
        re.search(r"\b(nationality|citizen(?:ship)?)\b", option, re.IGNORECASE)
        and not re.search(r"\b(not|no)\b", option, re.IGNORECASE)
        for option in _options(field)
    )


def _in_education_section(field: dict[str, Any]) -> bool:
    return field.get("section") == "education"


# The exact-label map (`autofill.PROFILE_LABELS`): a normalized label, optionally
# led by "Your", named one profile column. Each becomes an anchored pattern.
LABELS: tuple[tuple[str, str, str | None], ...] = (
    ("first name|given name", "identity.first_name", None),
    ("last name|family name|surname", "identity.last_name", None),
    ("name|full name|legal name|full legal name", "identity.legal_name", None),
    ("preferred name", "identity.preferred_name", None),
    ("email|email address", "contact.email", None),
    ("phone|phone number", "contact.phone", None),
    ("address|street address|address line 1", "address.line_1", None),
    ("address line 2", "address.line_2", None),
    ("city", "address.city", None),
    ("state|state/province", "address.state", None),
    ("zip code|postal code", "address.postal_code", None),
    ("country", "address.country", None),
    ("linkedin|linkedin url", "contact.linkedin_url", None),
    ("github|github url", "contact.github_url", None),
    ("portfolio|portfolio url", "contact.portfolio_url", None),
    ("school|school name", "education.school_name", None),
    ("degree", "education.degree_type", None),
    ("field of study", "education.field_of_study", None),
    ("gpa", "education.gpa", None),
    ("graduation month|graduation date month", "education.graduation_date", "date_as_month_name"),
    ("graduation year|graduation date year", "education.graduation_date", "date_as_year"),
    (
        "years of professional experience|how many years of experience do you have\\?",
        "experience.years_professional",
        None,
    ),
    (
        "earliest start date|earliest available start date"
        "|what is your earliest available start date\\?",
        "availability.earliest_start_date",
        None,
    ),
    ("are you willing to relocate\\?", "preferences.willing_to_relocate", None),
    ("are you willing to travel\\?", "preferences.willing_to_travel", None),
    (
        "will you now or in the future require sponsorship\\?|do you require visa sponsorship\\?",
        "work_auth.requires_sponsorship",
        None,
    ),
    ("are you authorized to work in the united states\\?", "work_auth.authorized_us", None),
)

LABEL_RULES = (
    *(
        _rule(
            "label",
            labels.split("|", 1)[0].replace("\\?", "").replace("/", "_").replace(" ", "_"),
            rf"^(?:your )?(?:{labels})$",
            fact_key,
            formatter=formatter,
            note="exact profile label",
        )
        for labels, fact_key, formatter in LABELS
    ),
    _rule(
        "label",
        "education_end_month",
        r"^end date month$",
        "education.graduation_date",
        formatter="date_as_month_name",
        note="end date of the education entry",
        when=_in_education_section,
    ),
    _rule(
        "label",
        "education_end_year",
        r"^end date year$",
        "education.graduation_date",
        formatter="date_as_year",
        note="end date of the education entry",
        when=_in_education_section,
    ),
)

SALARY = r"(?=.*\b(salary|pay|compensation|wage)\b)"
OTHER_CURRENCY = r"(?!.*\b(eur|euro|gbp|pounds?|cad|canadian dollars?|aud|yen|jpy|inr)\b)"
STATED_RANGE = r"\b(acceptable|comfortable|work for you|within your expectations)\b"
AUTHORIZATION_EXPIRY = (
    r"\b(expir(?:e|es|ation)|valid (?:until|through)|end date)\b.*"
    r"\b(visa|permit|work authori[sz]ation)\b|"
    r"\b(visa|permit|work authori[sz]ation)\b.*"
    r"\b(expir(?:e|es|ation)|valid (?:until|through)|end date)\b"
)

# The priority policies (`patterns.resolve_priority_answer`).
PRIORITY_RULES = (
    _rule(
        "priority",
        "salary_stated_range",
        rf"^{SALARY}{OTHER_CURRENCY}(?=.*{STATED_RANGE})",
        "compensation.accepts_stated_range",
        note="answered Yes to a stated pay range",
    ),
    _rule(
        "priority",
        "salary",
        rf"^{SALARY}{OTHER_CURRENCY}(?!.*{STATED_RANGE})"
        r"(?=.*\b(annual|annually|yearly|per year|base salary|requirements?|expectations?"
        r"|desired|minimum)\b)",
        "compensation.minimum",
        note="hourly minimum, or annual at 2080 hours",
    ),
    _rule(
        "priority",
        "graduation",
        r"graduat",
        "education.graduation_date",
        note="rendered as a date, a month, a term, or month and year",
    ),
    _rule(
        "priority",
        "completed_coop_terms",
        r"^(?=.*\bcompleted?\b)(?=.*\bco[ -]?op\b)(?=.*\b(term|block|rotation)s?\b)"
        r"(?!.*\b(employer|company|internship|project)s?\b)",
        "coop.completed_rotations",
    ),
    _rule("priority", "notice_period", r"\bnotice period\b", "availability.notice_period_weeks"),
    _rule(
        "priority",
        "authorization_option",
        rf"^(?!.*({AUTHORIZATION_EXPIRY})).*\bauthori[sz]ation to work\b",
        "work_auth.us_citizen",
        formatter="citizenship_option",
        note="picked the citizenship option of a work-authorization choice",
        when=_offers_citizenship_option,
    ),
)

# The two `reuse_policy = 'pattern'` rows of the old answer library.
LIBRARY_RULES = (
    _rule(
        "library",
        "prior_internship",
        r"\b(previous|prior|past|any)\b.*\b(internship|intern|co-?op)\b"
        r"|\b(internship|co-?op)\b.*\bexperience\b",
        "experience.prior_internship",
        note="library row 77cc8fda",
    ),
    _rule(
        "library",
        "available_onsite",
        r"\bavailable to (work|start)\b.*\b(on-?site|in-?person|full-?time|hybrid|per week"
        r"|days a week)\b",
        "availability.onsite",
        note="library row 68b930e5",
    ),
)

SANCTIONED = r"(north korea|syria|iran|cuba|russia|belarus|sanction|export control|embargo)"

# The builtin rules (`patterns.BUILTIN_RULES`), patterns unchanged.
BUILTIN_RULES = (
    _rule(
        "builtin",
        "preferred_name",
        r"\b(preferred|nick|chosen)\s*(first\s*)?name\b|\bgo by\b",
        "identity.preferred_name",
    ),
    _rule("builtin", "linkedin", r"\blinked\s*in\b", "contact.linkedin_url"),
    _rule("builtin", "github", r"\bgit\s*hub\b", "contact.github_url"),
    _rule(
        "builtin",
        "website",
        r"\b(other|personal|portfolio)\b.*\b(website|url|site|link)\b|\bportfolio\b",
        "contact.portfolio_url",
        note="the old rule answered with the GitHub URL",
    ),
    _rule(
        "builtin",
        "school",
        r"\b(school|university|college|institution)\b.*\b(attend|enrolled|current)|"
        r"\b(current|most recent)\b.*\b(school|university|college)\b",
        "education.school_name",
    ),
    _rule(
        "builtin",
        "degree",
        r"\b(degree|level of education|highest education)\b"
        r".*\b(pursu|seeking|working|current|obtain)|"
        r"\b(current|highest)\b.*\bdegree\b",
        "education.degree_type",
    ),
    _rule(
        "builtin",
        "major",
        r"\b(field of study|major|area of study|concentration)\b",
        "education.field_of_study",
    ),
    _rule("builtin", "gpa", r"\bgpa\b|grade point average", "education.gpa"),
    _rule(
        "builtin",
        "employer",
        r"\b(current|most recent|last)\b.*\b(employer|company)\b",
        "employment.current_employer",
    ),
    _rule(
        "builtin",
        "location",
        r"^\s*(current )?location\s*(\(\s*city\s*\))?\s*$|\blocation\s*/\s*city\b|"
        r"\bcity\s*/\s*location\b|\bwhere are you (currently )?(located|based)\b|"
        r"\bcurrent (city|location)\b|\bcity (and|,) state\b",
        "address.location",
    ),
    _rule(
        "builtin",
        "years_experience",
        r"\byears of (professional |relevant |work )?experience\b",
        "experience.years_professional",
    ),
    _rule(
        "builtin",
        "authorized",
        r"^(?!.*\b(expir(?:e|es|ation)|valid (?:until|through)|end date)\b)"
        r".*\b(authori[sz]ed|authori[sz]ation|eligible|legally (able|permitted)) to work\b",
        "work_auth.authorized_us",
    ),
    _rule("builtin", "sponsorship", r"\bsponsor(ship)?\b", "work_auth.requires_sponsorship"),
    _rule(
        "builtin",
        "sanctioned_country",
        rf"\b(citizenship|citizen|residen(cy|t)|national)\b.*{SANCTIONED}"
        rf"|{SANCTIONED}.*\b(citizenship|citizen|residen(cy|t)|national)\b",
        "work_auth.sanctioned_country_citizen",
        note="the old rule answered No for a US citizen",
    ),
    _rule(
        "builtin",
        "us_citizen",
        r"^(?!.*"
        + SANCTIONED
        + r")(.*\b(u\.?s\.?|united states|american)\s+citizen(ship)?\b"
        + r"|.*\bcitizen of the (u\.?s\.?|united states)\b)",
        "work_auth.us_citizen",
    ),
    _rule(
        "builtin",
        "citizenship_country",
        r"^(?!.*" + SANCTIONED + r")(?!.*\b(u\.?s\.?|united states|american)\s+citizen)"
        r".*\b(country of citizenship|citizenship country|nationality|what is your citizenship)\b",
        "work_auth.citizenship_country",
    ),
    _rule(
        "builtin",
        "onsite_work",
        r"(?=.*\b(on[ -]?site|in[ -]?office|in[ -]?person|commut)\b)"
        r"(?=.*\b(willing|able|available|agree|reliably|relocat)\b).+",
        "availability.onsite",
        note="the old rule always answered Yes",
    ),
    _rule(
        "builtin",
        "relocate",
        r"^(?!.*\b(on[ -]?site|in[ -]?office|in[ -]?person|commut)\b)"
        r".*\b(willing|able|open) to relocat",
        "preferences.willing_to_relocate",
    ),
    _rule(
        "builtin",
        "travel",
        r"\b(willing|able|open) to travel\b",
        "preferences.willing_to_travel",
    ),
    _rule(
        "builtin",
        "adult",
        r"\b(18|eighteen) (years|or older)\b|\blegal (working )?age\b",
        "identity.adult",
        note="the old rule always answered Yes",
    ),
    _rule("builtin", "gender", r"\bgender\b|\bsex\b", "eeo.gender_identity"),
    _rule("builtin", "hispanic", r"\bhispanic\b|\blatin[oax]\b", "eeo.hispanic_latino"),
    _rule("builtin", "race", r"\b(race|ethnicity)\b", "eeo.race_ethnicities"),
    _rule("builtin", "veteran", r"\bveteran\b", "eeo.veteran_status"),
    _rule(
        "builtin",
        "skills",
        r"\b(technolog(y|ies)|tech(nology)? stacks?|programming languages|languages|"
        r"frameworks|tools|skills)\b.*\b(experience|familiar|proficien|comfortable|know|"
        r"used|worked)|\b(experience|familiar|proficien|comfortable)\w*\b.*\b(technolog|"
        r"languages|frameworks|tools|stacks?|skills)",
        "skills.all",
    ),
    _rule("builtin", "disability", r"\bdisabilit", "eeo.disability_status"),
)

WORKED_BEFORE_RULES = (
    _rule(
        "worked_before",
        "worked_before",
        r"\b(ever|previously|formerly|before)\b.*\b(work(ed)?|employ(ed|ment)|intern(ed)?"
        r"|contract(or|ed)?)\b"
        r"|\b(work(ed)?|employ(ed|ment)|intern(ed)?)\b.*\b(before|previously|in the past)\b",
        "employment.worked_for",
        note="the old rule answered No when the company was not on the resume",
    ),
)

RULES: tuple[ArchivedRule, ...] = (
    LABEL_RULES + PRIORITY_RULES + LIBRARY_RULES + BUILTIN_RULES + WORKED_BEFORE_RULES
)
