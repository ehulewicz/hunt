"""Office selection: which offered office or location to choose (design §9).

Reads the `preferences.location_strategy` fact. It applies only to a question
that asks you to choose among offered offices; "where are you located now" is
the address facts, never this (R15). Options are parsed into (city, state,
region) here, in code (R16); an option that cannot be parsed is treated as
outside the US.

Single choice: a tier 1 city, else a tier 2 state, else a tier 3 region (the
preferred region first), else Remote (R19), else — only for a required field —
a US-East or Eastern Time option, else the first option. Texas and the South
Central states sit outside every tier (R17); Washington counts only as Seattle
(R18). Several choices (R20): "select all" takes every tier 1 and tier 2 match;
"up to k" fills k slots with tier 1 then tier 2. Random draws are seeded by the
application, so a replay of the same application picks the same office.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}
STATE_CODES = {name.casefold(): code for code, name in STATES.items()} | {
    code.casefold(): code for code in STATES
}
# R16: US-East is the Northeast and the Atlantic Southeast; US-Midwest the census
# Midwest; US-West the Pacific and Mountain states. The rest of the South — Texas
# and its neighbours among them — belongs to no region a tier names (R17).
REGIONS = {
    "US-East": {
        "ME", "NH", "VT", "MA", "RI", "CT", "NY", "NJ", "PA", "DE", "MD", "DC", "VA", "WV",
        "NC", "SC", "GA", "FL",
    },
    "US-Midwest": {"OH", "IN", "IL", "MI", "WI", "MN", "IA", "MO", "ND", "SD", "NE", "KS"},
    "US-West": {"WA", "OR", "CA", "AK", "HI", "NV", "ID", "MT", "WY", "UT", "CO", "AZ", "NM"},
}
EASTERN_TIME = {
    "CT", "DE", "DC", "FL", "GA", "IN", "KY", "ME", "MD", "MA", "MI", "NH", "NJ", "NY", "NC",
    "OH", "PA", "RI", "SC", "TN", "VT", "VA", "WV",
}
# Common names for a city that carry no state.
CITY_ALIASES = {
    "nyc": ("New York", "NY"),
    "new york city": ("New York", "NY"),
    "manhattan": ("New York", "NY"),
    "brooklyn": ("New York", "NY"),
    "sf": ("San Francisco", "CA"),
    "sf bay area": ("San Francisco", "CA"),
    "san francisco bay area": ("San Francisco", "CA"),
    "bay area": ("San Francisco", "CA"),
    "atl": ("Atlanta", "GA"),
}
REMOTE = re.compile(r"\bremote\b", re.IGNORECASE)
UP_TO = re.compile(
    r"\b(?:up to|select|choose|pick|rank(?: your)?(?: top)?|top)\s+(\d+|two|three|four|five)\b",
    re.IGNORECASE,
)
NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5}


@dataclass(frozen=True, slots=True)
class Place:
    option: str
    city: str | None
    state: str | None
    remote: bool = False

    @property
    def region(self) -> str | None:
        return next((name for name, states in REGIONS.items() if self.state in states), None)


def parse(option: str) -> Place:
    """An option as a city and state; neither when it cannot be read as a US place."""
    text = " ".join(option.replace("\u2013", "-").split())
    if REMOTE.search(text):
        return Place(option, None, None, remote=True)
    lowered = text.casefold().strip(" .")
    if lowered in CITY_ALIASES:
        city, state = CITY_ALIASES[lowered]
        return Place(option, city, state)
    parts = [part.strip(" .") for part in re.split(r"[,(]|\s-\s", text) if part.strip(" .)")]
    parts = [part.rstrip(")") for part in parts]
    if len(parts) >= 2:
        code = STATE_CODES.get(parts[1].casefold())
        if code is not None:
            alias = CITY_ALIASES.get(parts[0].casefold())
            return Place(option, alias[0] if alias else parts[0], code)
    if lowered in STATE_CODES:
        return Place(option, None, STATE_CODES[lowered])
    return Place(option, None, None)


def _tier_one(place: Place, strategy: dict[str, Any]) -> bool:
    for wanted in strategy.get("tier_1_preferred_cities") or []:
        if not isinstance(wanted, dict):
            continue
        if (
            place.city is not None
            and place.city.casefold() == str(wanted.get("city", "")).casefold()
            and place.state == str(wanted.get("state", "")).upper()
        ):
            return True
    return False


def _tier_two(place: Place, strategy: dict[str, Any]) -> bool:
    states = {str(state).upper() for state in strategy.get("tier_2_state_fallbacks") or []}
    return place.state in states


def _regions(strategy: dict[str, Any]) -> list[str]:
    fallbacks = strategy.get("tier_3_region_fallbacks") or {}
    if not isinstance(fallbacks, dict):
        return []
    preferred = str(fallbacks.get("preferred_region") or "")
    acceptable = [str(region) for region in fallbacks.get("acceptable_regions") or []]
    if not preferred:
        return acceptable
    return [preferred, *(region for region in acceptable if region != preferred)]


def limit_of(question: str) -> int | None:
    """How many choices a "select up to k" question allows; None when it does not say."""
    found = UP_TO.search(question)
    if found is None:
        return None
    word = found.group(1).casefold()
    return int(word) if word.isdigit() else NUMBERS.get(word)


def choose_offices(
    options: list[str],
    strategy: Any,
    *,
    seed: str,
    multiple: bool = False,
    question: str = "",
    required: bool = True,
) -> list[str]:
    """The options to choose, in the order chosen; empty when the policy picks none."""
    if not isinstance(strategy, dict) or not options:
        return []
    draw = random.Random(f"{seed}|{question}")
    places = [parse(option) for option in options]

    def shuffled(items: list[Place]) -> list[Place]:
        copy = list(items)
        draw.shuffle(copy)
        return copy

    tier_one = [place for place in places if _tier_one(place, strategy)]
    tier_two = [
        place for place in places if not _tier_one(place, strategy) and _tier_two(place, strategy)
    ]
    if multiple:
        limit = limit_of(question)
        ordered = shuffled(tier_one) + shuffled(tier_two)
        if limit is not None:
            ordered = ordered[:limit]
        if ordered:
            return [place.option for place in ordered]
    for tier in (tier_one, tier_two):
        if tier:
            return [shuffled(tier)[0].option]
    for region in _regions(strategy):
        inside = [place for place in places if place.region == region]
        if inside:
            return [shuffled(inside)[0].option]
    remote = [place for place in places if place.remote]
    if remote:
        return [remote[0].option]
    if not required:
        return []
    eastern = [
        place
        for place in places
        if place.region == "US-East" or (place.state is not None and place.state in EASTERN_TIME)
    ]
    if eastern:
        return [shuffled(eastern)[0].option]
    return [options[0]]
