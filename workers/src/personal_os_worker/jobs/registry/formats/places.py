"""Formatters: one fact, rendered for the control in front of it (design §8).

A fact is stored once; "Fall 2027", "December 2027", "12/15/2027", and the
"Fall 2027" option of a term dropdown are renderings of one graduation date,
produced at fill time. A formatter is chosen from the fact's type, the
question's wording, the control, and — when the control offers options — which
rendering one option actually matches. Every rendering names its formatter, so a
value that fits no option is a `format` failure that can be attributed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from personal_os_worker.jobs.facts.derived import graduation_term
from personal_os_worker.jobs.office import STATE_CODES, STATES
from personal_os_worker.jobs.options import choose_option, normalize_answer_question
from personal_os_worker.jobs.registry.formats import Rendering
from personal_os_worker.jobs.registry.formats.choices import _yes_no_pair, yes_no_option

_STATE_CODE = re.compile(r"^[A-Z]{2}$")


def _state_option(options: list[str], value: str) -> Rendering | None:
    """A menu of postal codes takes the state's code ("Georgia" -> "GA"); a
    code-valued fact against a menu of names takes the name."""
    text = value.strip()
    coded = [option for option in options if _STATE_CODE.fullmatch(option.strip())]
    if len(coded) >= 2 and len(coded) > len(options) / 2:
        code = STATE_CODES.get(text.casefold())
        found = next(
            (option for option in options if option.strip() == code), None
        )
        if found is not None:
            return Rendering(found, "state_code_option")
    if STATE_CODES.get(text.casefold()) == text.upper():
        name = STATES.get(text.upper())
        if name is not None:
            chosen = choose_option(options, name)
            if chosen is not None:
                return Rendering(chosen, "state_name_option")
    return None

def _states_named(text: str) -> set[str]:
    """The US states a question names by their full name."""
    lowered = text.casefold()
    return {
        name
        for name in STATES.values()
        if re.search(rf"(?<![a-z]){re.escape(name.casefold())}(?![a-z])", lowered)
    }


_SCHOOL = re.compile(
    r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\s+"
    r"(University|College|Institute of Technology|Polytechnic Institute|Tech)\b"
    r"|\bUniversity\s+of\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)"
)


def _school_named(question: str) -> str | None:
    """The school a question names ("Northeastern University", "University of X")."""
    found = _SCHOOL.search(question)
    if found is None:
        return None
    if found.group(1):
        return f"{found.group(1)} {found.group(2)}"
    return f"University of {found.group(3)}"


def _school_forms(name: str) -> set[str]:
    """The ways the fact's school is written ("Example Institute of Technology",
    "Example University")."""
    forms = {name}
    forms.add(
        re.sub(r"\binstitute of technology\b", "Tech", name, flags=re.IGNORECASE)
    )
    return {" ".join(form.casefold().split()) for form in forms}

COUNTRIES: dict[str, tuple[str, ...]] = {
    "united states": (
        "united states", "united states of america", "usa", "u s a", "america",
    ),
    "canada": ("canada",),
    "mexico": ("mexico",),
    "united kingdom": ("united kingdom", "uk", "u k", "great britain", "britain"),
    "ireland": ("ireland",),
    "germany": ("germany",),
    "france": ("france",),
    "netherlands": ("netherlands", "holland"),
    "spain": ("spain",),
    "italy": ("italy",),
    "switzerland": ("switzerland",),
    "sweden": ("sweden",),
    "poland": ("poland",),
    "india": ("india",),
    "china": ("china",),
    "japan": ("japan",),
    "south korea": ("south korea", "korea"),
    "singapore": ("singapore",),
    "australia": ("australia",),
    "new zealand": ("new zealand",),
    "brazil": ("brazil",),
    "israel": ("israel",),
    "united arab emirates": ("united arab emirates", "uae", "u a e"),
    "taiwan": ("taiwan",),
    "hong kong": ("hong kong",),
    "philippines": ("philippines",),
    "vietnam": ("vietnam", "viet nam"),
    "indonesia": ("indonesia",),
    "malaysia": ("malaysia",),
    "pakistan": ("pakistan",),
    "nigeria": ("nigeria",),
}
# "US" unadorned is a pronoun as often as a country; it counts only as "the US",
# with periods, or spelled out.
_US_PATTERN = re.compile(
    r"\bthe\s+u\.?s\.?\b|\bu\.s\.(?:a\.?)?\b|\busa\b"
    r"|\bunited states\b|\bamerica\b",
    re.IGNORECASE,
)


def _canon_country(value: str) -> str | None:
    """The canonical name for a fact's country, or None when it names none."""
    normalized = " ".join(str(value).casefold().split()).replace(".", "")
    for country, aliases in COUNTRIES.items():
        if normalized in aliases:
            return country
    if normalized in {"us", "u s", "u s a"}:
        return "united states"
    return None


def _named_country(text: str) -> str | None:
    """The one listed country a question names, or None."""
    if _US_PATTERN.search(text):
        return "united states"
    lowered = text.casefold().replace(".", "")
    for country, aliases in COUNTRIES.items():
        if country == "united states":
            continue
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", lowered):
                return country
    return None


_NAMED_TERM = re.compile(
    r"\b(winter|spring|summer|fall|autumn)\s*(\d{4})\b", re.IGNORECASE
)
_COMPLETION_WORD = re.compile(
    r"\b(complet\w*|graduat\w*|finish\w*|earn\w*|conferr\w*)\b", re.IGNORECASE
)
_ENROLLMENT_WORD = re.compile(
    r"\b(return\w*|enroll\w*|attend\w*|back to school|in school|continu\w*)\b",
    re.IGNORECASE,
)
_TERM_STARTS = {"winter": (1, 1), "spring": (1, 5), "summer": (5, 15), "fall": (8, 15)}

def _comparison(
    key: str, value: Any, question: str, options: list[str]
) -> Rendering | None:
    """A yes/no (or a None option) decided by what the question names: a state,
    a school, a country, or a term, checked against the fact it asks about."""
    if key == "address.state" and _yes_no_pair(options):
        named = _states_named(question)
        if named:
            mine = STATE_CODES.get(str(value).strip().casefold())
            chosen = yes_no_option(
                options,
                mine in {STATE_CODES.get(name.casefold()) for name in named},
            )
            if chosen is not None:
                return Rendering(chosen, "named_state_option")
    if key == "education.school_name" and _yes_no_pair(options):
        named_school = _school_named(question)
        if named_school is not None:
            answer = (
                " ".join(named_school.casefold().split())
                in _school_forms(str(value))
            )
            chosen = yes_no_option(options, answer)
            if chosen is not None:
                return Rendering(chosen, "named_school_option")
    if key in {"work_auth.citizenship_country", "address.country"}:
        asked = _named_country(question)
        if asked is not None:
            mine = _canon_country(str(value))
            if mine is not None and _yes_no_pair(options):
                chosen = yes_no_option(options, asked == mine)
                if chosen is not None:
                    return Rendering(chosen, "named_country_option")
            if mine is not None and asked != mine:
                none_option = next(
                    (
                        option
                        for option in options
                        if normalize_answer_question(option)
                        in {"none", "n a", "not applicable"}
                    ),
                    None,
                )
                if none_option is not None:
                    return Rendering(none_option, "named_country_option")
    if key == "education.graduation_date" and _yes_no_pair(options):
        named_terms = [
            ("fall" if term.casefold() == "autumn" else term.casefold(), int(year))
            for term, year in _NAMED_TERM.findall(question)
        ]
        if named_terms:
            try:
                when = date.fromisoformat(str(value))
            except ValueError:
                when = None
            term_answer: bool | None = None
            if when is not None and _COMPLETION_WORD.search(question):
                terms = {f"{graduation_term(when)} {when.year}"}
                if when.month >= 11:
                    terms.add(f"Winter {when.year}")
                term_answer = any(
                    f"{term.title()} {year}" in terms for term, year in named_terms
                )
            elif when is not None and _ENROLLMENT_WORD.search(question):
                earliest = min(
                    date(year, *_TERM_STARTS[term]) for term, year in named_terms
                )
                term_answer = when >= earliest
            if term_answer is not None:
                chosen = yes_no_option(options, term_answer)
                if chosen is not None:
                    return Rendering(chosen, "named_term_option")
    return None

def _city_with_state(key: str, value: Any, context: Mapping[str, Any] | None) -> str | None:
    """"Atlanta, Georgia": a city with the state that tells it from its namesakes."""
    state = (context or {}).get("address.state")
    if key != "address.city" or not state or not str(value).strip():
        return None
    return f"{str(value).strip()}, {state}"
