"""Engineering focus: which offered discipline to choose.

Reads the `preferences.engineering_focus` fact — your engineering interests as
an unordered set, not a ranking. It applies only to a question that asks you to
choose among offered disciplines; "what do you want to work on" in prose is the
approved writing, never this.

A choice question takes, in order: an option that fits the posting — the title
names it, or shares one of its engineering families — and is also one of your
interests; then an option the title names; then an option whose family the
title shares; then an interest the menu offers; then any real option. The
title "names" an option when every word that makes the option distinct —
anything but engineer, developer, intern and the like — appears in it, which is
why iOS beats Android for "..., iOS" while "Full-Time" does not name "Full
Stack"; family matching is how "Frontend Engineer (React, TypeScript)" still
fits a "Frontend Engineer Intern" posting. "Other", "N/A" and "no preference"
are never chosen. A "second choice" question takes the next item of the same
ranking, so the questions of one application share one draw: the seed is the
application's, never the question's. Several choices take every offered option
the title or the interests match, in page order. A free-text box gets the name
of the one interest the title's families decide, else a drawn one.
"""

from __future__ import annotations

import random
import re

FAMILIES: dict[str, re.Pattern[str]] = {
    "backend": re.compile(r"\bback[\s-]?end\b", re.IGNORECASE),
    "frontend": re.compile(r"\bfront[\s-]?end\b|\bui\b|\bweb\b", re.IGNORECASE),
    "full_stack": re.compile(r"\bfull[\s-]?stack\b", re.IGNORECASE),
    "mobile": re.compile(r"\bmobile\b|\bios\b|\bandroid\b", re.IGNORECASE),
    "ml": re.compile(
        r"\bmachine learning\b|\bml\b|\bai\b|\bartificial intelligence\b"
        r"|\bdeep learning\b|\bllms?\b|\bdata scien\w*",
        re.IGNORECASE,
    ),
    "data": re.compile(
        r"\bdata engineer\w*|\bbig data\b|\bdata platform\b|\banalytics\b", re.IGNORECASE
    ),
    "forward_deployed": re.compile(r"\bforward[\s-]?deployed\b|\bfde\b", re.IGNORECASE),
    "simulation": re.compile(r"\bsimulation\b|\bmodell?ing\b", re.IGNORECASE),
    "scientific": re.compile(
        r"\bscientific\b|\bnumerical\b|\bhigh[\s-]performance computing\b|\bhpc\b",
        re.IGNORECASE,
    ),
    "algorithms": re.compile(r"\balgorithms?\b", re.IGNORECASE),
    "robotics": re.compile(r"\brobot\w*|\bautonom\w*|\bperception\b", re.IGNORECASE),
    "research": re.compile(r"\bresearch\b", re.IGNORECASE),
    "applied": re.compile(r"\bapplied\b", re.IGNORECASE),
    "software": re.compile(
        r"\bsoftware (?:engineer\w*|development|developer)\b|\bswe\b", re.IGNORECASE
    ),
    "systems": re.compile(r"\bsystems?\b|\bplatform\b|\binfrastructure\b|\binfra\b", re.IGNORECASE),
    "security": re.compile(r"\bsecurity\b|\bcyber\w*", re.IGNORECASE),
    "embedded": re.compile(r"\bembedded\b|\bfirmware\b", re.IGNORECASE),
}
# Words that tell one discipline option from another never include the role
# itself: "Backend Engineer" is named by a title that says "backend".
GENERIC_WORDS = frozenset(
    {
        "engineer", "engineering", "developer", "development", "intern", "internship",
        "co", "op", "coop", "role", "roles", "position", "job", "team", "the", "a",
        "an", "and", "of", "for", "in", "to", "at",
    }
)
# A menu's way of saying "none of the above" is not a discipline; nor is a
# dashes-only placeholder like JazzHR's "-- No answer --".
NON_OPTION = re.compile(
    r"^\s*(?:(?:other|none|n/?a|not applicable|no preference|either|both|any"
    r"|prefer not|i don'?t know|select)\b|-{2,})",
    re.IGNORECASE,
)


def families(text: str) -> set[str]:
    """The engineering families a text names."""
    return {name for name, pattern in FAMILIES.items() if pattern.search(text)}


def _real(options: list[str]) -> list[str]:
    return [option for option in options if not NON_OPTION.search(option)]


def _title_names(option: str, title: str) -> bool:
    """Whether the title names the option: every distinguishing word appears in it."""
    words = [
        word
        for word in re.findall(r"[a-z0-9]+", option.casefold())
        if word not in GENERIC_WORDS
    ]
    if not words:
        return False
    named = set(re.findall(r"[a-z0-9]+", title.casefold()))
    return all(word in named for word in words)


def _tiers(options: list[str], interests: list[str], title: str) -> tuple[list[str], ...]:
    named = families(title)
    wanted: set[str] = set()
    for interest in interests:
        wanted |= families(interest)
    tiers: list[list[str]] = [[], [], [], [], []]
    for option in options:
        lexical = _title_names(option, title)
        family = bool(families(option) & named)
        interest_hit = bool(families(option) & wanted)
        if (lexical or family) and interest_hit:
            tiers[0].append(option)
        elif lexical:
            tiers[1].append(option)
        elif family:
            tiers[2].append(option)
        elif interest_hit:
            tiers[3].append(option)
        else:
            tiers[4].append(option)
    return tuple(tiers)


def focus_ranking(
    options: list[str], interests: list[str], *, title: str, seed: str
) -> list[str]:
    """The real options tiered by title and interests, shuffled per application."""
    draw = random.Random(f"{seed}|engineering_focus")
    ranking: list[str] = []
    for tier in _tiers(_real(options), interests, title):
        ordered = sorted(tier)
        draw.shuffle(ordered)
        ranking.extend(ordered)
    return ranking


def choose_focus(
    options: list[str],
    interests: list[str],
    *,
    title: str,
    seed: str,
    question: str = "",
    multiple: bool = False,
) -> list[str]:
    """The options to choose; empty when the menu offers nothing real."""
    real = _real(options)
    if not real:
        return []
    ranking = focus_ranking(options, interests, title=title, seed=seed)
    if multiple:
        tiers = _tiers(real, interests, title)
        wanted = set().union(*(set(tier) for tier in tiers[:4]))
        picked = [option for option in real if option in wanted]
        return picked or [ranking[0]]
    if re.search(r"\b(second|2nd)\b", question, re.IGNORECASE):
        index = 1
    elif re.search(r"\b(third|3rd)\b", question, re.IGNORECASE):
        index = 2
    else:
        index = 0
    return [ranking[min(index, len(ranking) - 1)]]


def focus_text(interests: list[str], *, title: str, seed: str) -> str | None:
    """The one interest a free-text box gets; None when there are none."""
    named = families(title)
    matching = [interest for interest in interests if families(interest) & named]
    pool = matching or [interest for interest in interests if str(interest).strip()]
    if not pool:
        return None
    return random.Random(f"{seed}|engineering_focus").choice(pool)
