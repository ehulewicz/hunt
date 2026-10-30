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
from typing import Any

from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.registry.formats import _words
from personal_os_worker.jobs.sites import is_non_answer_option

# Consent, terms, and attestations are agreed to (decided 2026-09-14), through
# the `consent.agrees` fact: the option that agrees, whatever it says it with.
NEGATION = re.compile(r"\b(not|no|don t|do not|decline|disagree|reject|refuse)\b")
AFFIRMATION = re.compile(
    r"\b(yes|agree|accept|acknowledge|consent|confirm|i have read|understood)\b"
)
# How you came to a posting, through `preferences.application_source`: the
# employer's own site, then a job board, then Other (a referral first when the
# application was referred).
JOB_BOARD = re.compile(r"\b(job\s+boards?|linkedin|indeed|handshake|glassdoor|wellfound|dice)\b")
# A career fair, a campus career office or an info session is an event or a
# place, never the employer's own site; nor is a school's own careers site.
NOT_A_SITE = frozenset(
    {"fair", "fairs", "office", "event", "events", "center", "centre", "services", "session",
     "expo", "seminar", "conference", "university", "college", "campus", "school"}
)
# "Careers", "Nintendo Careers", "Careers page": the employer's careers site by name.
CAREERS_SITE = re.compile(r"\bcareers?(?:\s+(?:page|site|portal))?$")
NEGATIVE = re.compile(r"\b(no|not|don t|do not|never|none|neither)\b")
AFFIRMATIVE = re.compile(r"^(yes|true|y)$")
NEGATIVE_WORD = re.compile(r"^(no|false|n)$")

def affirmative_option(options: list[str]) -> str | None:
    """The one option that agrees: "Yes", "I agree", "Yes, I have read and accept ..."."""
    candidates = [
        option
        for option in options
        if option.strip()
        and not is_non_answer_option(option)
        and not NEGATION.search(normalize_answer_question(option))
    ]
    agreeing = [
        option for option in candidates if AFFIRMATION.search(normalize_answer_question(option))
    ]
    if len(agreeing) == 1:
        return agreeing[0]
    return candidates[0] if len(candidates) == 1 and not agreeing else None

def _is_company_site(value: str) -> bool:
    """"Company Website", "Point72 Website", "Nintendo Careers": the employer's own site."""
    words = set(value.split())
    if JOB_BOARD.search(value) or words & NOT_A_SITE:
        return False
    if not (words & {"website", "site", "webpage", "web"} or CAREERS_SITE.search(value)):
        return False
    return "job" not in words or "company" in words


def source_option(options: list[str], *, referred: bool = False) -> str | None:
    """Your standing source, ranked by the options actually offered.

    A referred application names the referral first.
    """
    normalized = [(option, normalize_answer_question(option)) for option in options]
    if referred:
        named = [
            option for option, value in normalized
            if re.search(r"\breferr(al|ed)\b", value) and not re.search(r"\bnot\b", value)
        ]
        if named:
            return named[0]
    preferences = (
        _is_company_site,
        lambda value: re.search(r"\bjob\s+boards?\b", value) is not None,
        lambda value: value == "other" or value.startswith("other "),
    )
    for matches in preferences:
        found = [option for option, value in normalized if matches(value)]
        if found:
            return found[0]
    return None

def polarity(value: Any) -> bool | None:
    """True or False for a yes/no value, typed or written; None for anything else."""
    if isinstance(value, bool):
        return value
    text = _words(str(value))
    if AFFIRMATIVE.match(text):
        return True
    if NEGATIVE_WORD.match(text):
        return False
    return None


def yes_no_option(options: list[str], answer: bool) -> str | None:
    """The one option that says yes (or no), whatever words it says it with.

    "No, I am not a protected veteran" and "I don't have a disability" say no;
    "Yes", "I identify as one or more of the classifications" say yes. Options
    that decline to answer never count.
    """
    eligible = [option for option in options if not is_non_answer_option(option)]
    opener = ["yes"] if answer else ["no"]
    leading = [option for option in eligible if _words(option).split()[:1] == opener]
    if len(leading) == 1:
        return leading[0]
    if answer and len(leading) > 1:
        # "Yes - Somerville only", "Yes - Devens only", "Yes - either works": a
        # yes with no conditions is the one that leaves every place open.
        open_ended = [
            option for option in leading
            if re.search(r"\b(either|any|all|both|flexible|anywhere|open to)\b", _words(option))
        ]
        if len(open_ended) == 1:
            return open_ended[0]
    if answer:
        hits = [option for option in eligible if not NEGATIVE.search(_words(option))]
    else:
        hits = [option for option in eligible if NEGATIVE.search(_words(option))]
    return hits[0] if len(hits) == 1 else None

def _yes_no_pair(options: list[str]) -> bool:
    """Whether the options offer both a yes and a no."""
    return yes_no_option(options, True) is not None and yes_no_option(
        options, False
    ) is not None
