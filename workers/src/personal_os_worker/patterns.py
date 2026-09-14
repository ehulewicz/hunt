"""Deterministic pattern rules for short and choice questions.

Two kinds of rule answer a question whose literal text has no exact library
answer. Built-in rules recognise the general shape of a question and answer it
from profile facts or the canonical resume, so "Preferred first name", "LinkedIn
URL", "Are you a US citizen?", and "Have you ever worked for Acme?" resolve on
any form. Library rules are rows with `reuse_policy = 'pattern'` whose
`normalized_question` is a case-insensitive regular expression and whose
`answer_text` is the answer; they hold the user's standing policy answers.

Exactly one matching rule answers. Zero or several leave the field for the
next tier, so a question about citizenship in a sanctioned country is never
confused with a question about being a citizen.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Resolver = Callable[["RuleContext"], str | None]


@dataclass(frozen=True, slots=True)
class RuleContext:
    profile: dict[str, Any]
    resume: dict[str, Any]
    company: str


@dataclass(frozen=True, slots=True)
class BuiltinRule:
    name: str
    pattern: re.Pattern[str]
    resolve: Resolver


def _text(profile: dict[str, Any], key: str) -> str | None:
    value = str(profile.get(key) or "").strip()
    return value or None


def _country(profile: dict[str, Any]) -> str | None:
    country = _text(profile, "country")
    if country and country.casefold().startswith("united stat"):
        return "United States"
    return country


def _is_citizen(profile: dict[str, Any]) -> bool:
    visa = str(profile.get("visa_status") or "").casefold()
    return "citizen" in visa


def _location(context: RuleContext) -> str | None:
    parts = [
        _text(context.profile, "city"),
        _text(context.profile, "region"),
        _country(context.profile),
    ]
    present = [part for part in parts if part]
    return ", ".join(present) if present else None


def _yes_if(flag: bool) -> str | None:
    return "Yes" if flag else None


def _no_if(flag: bool) -> str | None:
    return "No" if flag else None


def _bool_answer(profile: dict[str, Any], key: str, *, invert: bool = False) -> str | None:
    value = profile.get(key)
    if not isinstance(value, bool):
        return None
    return "Yes" if value != invert else "No"


def _current_employer(context: RuleContext) -> str | None:
    experience = context.resume.get("experience") or []
    if not experience:
        return None
    org = str(experience[0].get("org") or "").strip()
    return org or None


def _hispanic(context: RuleContext) -> str | None:
    races = context.profile.get("eeo_race_ethnicities")
    if not isinstance(races, list) or not races:
        return None
    return "Yes" if any("hispanic" in str(race).casefold() for race in races) else "No"


def _race(context: RuleContext) -> str | None:
    races = context.profile.get("eeo_race_ethnicities")
    if isinstance(races, list) and len(races) == 1:
        return str(races[0])
    return None


def _skills(context: RuleContext) -> str | None:
    """Every entry of the resume's Skills section, comma-joined, in order."""
    names: list[str] = []
    for group in context.resume.get("skills") or []:
        for item in str(group.get("items") or "").split(","):
            name = item.strip()
            if name and name not in names:
                names.append(name)
    return ", ".join(names) if names else None


def _rule(name: str, pattern: str, resolve: Resolver) -> BuiltinRule:
    return BuiltinRule(name, re.compile(pattern, re.IGNORECASE | re.DOTALL), resolve)


SANCTIONED = r"(north korea|syria|iran|cuba|russia|belarus|sanction|export control|embargo)"

BUILTIN_RULES: tuple[BuiltinRule, ...] = (
    _rule(
        "preferred_name",
        r"\b(preferred|nick|chosen)\s*(first\s*)?name\b|\bgo by\b",
        lambda c: _text(c.profile, "preferred_name"),
    ),
    _rule("linkedin", r"\blinked\s*in\b", lambda c: _text(c.profile, "linkedin_url")),
    _rule("github", r"\bgit\s*hub\b", lambda c: _text(c.profile, "github_url")),
    _rule(
        "website",
        r"\b(other|personal|portfolio)\b.*\b(website|url|site|link)\b|\bportfolio\b",
        lambda c: _text(c.profile, "github_url"),
    ),
    _rule(
        "school",
        r"\b(school|university|college|institution)\b.*\b(attend|enrolled|current)|"
        r"\b(current|most recent)\b.*\b(school|university|college)\b",
        lambda c: _text(c.profile, "school_name"),
    ),
    _rule(
        "degree",
        r"\b(degree|level of education|highest education)\b"
        r".*\b(pursu|seeking|working|current|obtain)|"
        r"\b(current|highest)\b.*\bdegree\b",
        lambda c: _text(c.profile, "degree_type"),
    ),
    _rule(
        "major",
        r"\b(field of study|major|area of study|concentration)\b",
        lambda c: _text(c.profile, "field_of_study"),
    ),
    _rule("gpa", r"\bgpa\b|grade point average", lambda c: _text(c.profile, "gpa")),
    _rule(
        "employer",
        r"\b(current|most recent|last)\b.*\b(employer|company)\b",
        _current_employer,
    ),
    _rule(
        "location",
        r"^\s*(current )?location\s*(\(\s*city\s*\))?\s*$|\blocation\s*/\s*city\b|"
        r"\bcity\s*/\s*location\b|\bwhere are you (currently )?(located|based)\b|"
        r"\bcurrent (city|location)\b|\bcity (and|,) state\b",
        _location,
    ),
    _rule(
        "years_experience",
        r"\byears of (professional |relevant |work )?experience\b",
        lambda c: _text(c.profile, "years_professional_experience"),
    ),
    _rule(
        "authorized",
        r"\b(authori[sz]ed|authori[sz]ation|eligible|legally (able|permitted)) to work\b",
        lambda c: _yes_if(
            str(c.profile.get("work_authorization_status") or "").casefold() == "yes"
        ),
    ),
    _rule(
        "sponsorship",
        r"\bsponsor(ship)?\b",
        lambda c: _bool_answer(c.profile, "sponsorship_required"),
    ),
    _rule(
        "sanctioned_country",
        rf"\b(citizenship|citizen|residen(cy|t)|national)\b.*{SANCTIONED}|{SANCTIONED}.*\b(citizenship|citizen|residen(cy|t)|national)\b",
        lambda c: _no_if(_is_citizen(c.profile)),
    ),
    _rule(
        "us_citizen",
        r"^(?!.*"
        + SANCTIONED
        + r")(.*\b(u\.?s\.?|united states|american)\s+citizen(ship)?\b"
        + r"|.*\bcitizen of the (u\.?s\.?|united states)\b)",
        lambda c: _yes_if(_is_citizen(c.profile)),
    ),
    _rule(
        "citizenship_country",
        r"^(?!.*" + SANCTIONED + r")(?!.*\b(u\.?s\.?|united states|american)\s+citizen)"
        r".*\b(country of citizenship|citizenship country|nationality|what is your citizenship)\b",
        lambda c: "United States" if _is_citizen(c.profile) else None,
    ),
    _rule(
        "relocate",
        r"\b(willing|able|open) to relocat",
        lambda c: _bool_answer(c.profile, "willing_to_relocate"),
    ),
    _rule(
        "travel",
        r"\b(willing|able|open) to travel\b",
        lambda c: _bool_answer(c.profile, "willing_to_travel"),
    ),
    _rule("adult", r"\b(18|eighteen) (years|or older)\b|\blegal (working )?age\b", lambda c: "Yes"),
    _rule("gender", r"\bgender\b|\bsex\b", lambda c: _text(c.profile, "eeo_gender_identity")),
    _rule("hispanic", r"\bhispanic\b|\blatin[oax]\b", _hispanic),
    _rule("race", r"\b(race|ethnicity)\b", _race),
    _rule("veteran", r"\bveteran\b", lambda c: _text(c.profile, "eeo_veteran_status")),
    _rule(
        "skills",
        r"\b(technolog(y|ies)|tech(nology)? stacks?|programming languages|languages|"
        r"frameworks|tools|skills)\b.*\b(experience|familiar|proficien|comfortable|know|"
        r"used|worked)|\b(experience|familiar|proficien|comfortable)\w*\b.*\b(technolog|"
        r"languages|frameworks|tools|stacks?|skills)",
        _skills,
    ),
    _rule("disability", r"\bdisabilit", lambda c: _text(c.profile, "eeo_disability_status")),
)

WORKED_BEFORE = re.compile(
    r"\b(ever|previously|formerly|before)\b.*\b(work(ed)?|employ(ed|ment)|intern(ed)?|contract(or|ed)?)\b"
    r"|\b(work(ed)?|employ(ed|ment)|intern(ed)?)\b.*\b(before|previously|in the past)\b",
    re.IGNORECASE | re.DOTALL,
)


def company_on_resume(company: str, resume_orgs: frozenset[str]) -> bool:
    needle = company.strip().casefold()
    return bool(needle) and any(needle in org or org in needle for org in resume_orgs)


def resume_organizations(resume: dict[str, Any]) -> frozenset[str]:
    """Every organization named on the canonical resume, casefolded."""
    names: set[str] = set()
    for section in ("experience", "research", "projects"):
        for entry in resume.get(section) or []:
            org = str(entry.get("org") or "").strip().casefold()
            if org:
                names.add(org)
    return frozenset(names)


def library_pattern_answers(question: str, rules: list[dict[str, Any]]) -> list[str]:
    matches: list[str] = []
    for rule in rules:
        if str(rule.get("reuse_policy") or "") != "pattern":
            continue
        source = str(rule.get("normalized_question") or "")
        answer = str(rule.get("answer_text") or "").strip()
        if not source or not answer:
            continue
        try:
            if re.search(source, question, re.IGNORECASE | re.DOTALL):
                matches.append(answer)
        except re.error:
            continue
    return matches


def builtin_answers(question: str, context: RuleContext) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for rule in BUILTIN_RULES:
        if rule.pattern.search(question):
            answer = rule.resolve(context)
            if answer:
                found.append((rule.name, answer))
    return found


def resolve_pattern_answer(
    question: str,
    *,
    rules: list[dict[str, Any]],
    profile: dict[str, Any],
    resume: dict[str, Any],
    company: str,
) -> str | None:
    """Answer from exactly one library pattern, else exactly one built-in rule, else nothing."""
    library = library_pattern_answers(question, rules)
    if len(library) == 1:
        return library[0]
    if library:
        return None
    context = RuleContext(profile=profile, resume=resume, company=company)
    builtin = builtin_answers(question, context)
    if len(builtin) == 1:
        return builtin[0][1]
    if builtin:
        return None
    if WORKED_BEFORE.search(question) and not company_on_resume(
        company, resume_organizations(resume)
    ):
        return "No"
    return None
