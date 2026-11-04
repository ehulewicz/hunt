"""A Lever menu asks for your location in places wider or shorter than the whole fact."""

from __future__ import annotations

import re

from personal_os_worker.jobs.registry.api import Format, FormatInput

LOCATION_QUESTION = re.compile(r"\blocat(?:ion|ed)\b", re.IGNORECASE)
EXCLUDES = re.compile(r"\b(?:outside|not|non|except|excluding|other than)\b", re.IGNORECASE)
US_NAMES = {"united states", "united states of america", "usa", "us", "u s", "u s a"}
US_OPTION = re.compile(
    r"\b[Uu]nited [Ss]tates\b|\bU\.?S\.?(?:A\.?)?(?![A-Za-z])|\bNorth America\b|\bthe Americas\b"
)
STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def words(text: str) -> str:
    """Lowercase words with single spaces, padded so a phrase matches whole words."""
    return " " + re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip() + " "


def has_code(option: str, code: str) -> bool:
    """A postal code, in capitals only: "IN" is a state, "in" is a word."""
    return re.search(rf"\b{code}\b", option) is not None


def names_part(option: str, part: str) -> bool:
    """Whether the option names this one part of the fact, in any of its spellings."""
    if words(part).strip() in US_NAMES:
        return US_OPTION.search(option) is not None
    code = STATES.get(words(part).strip())
    if code is not None and has_code(option, code):
        return True
    return words(part) in words(option)


def names_other_state(option: str, parts: list[str]) -> bool:
    """Whether the option places itself in a state the fact does not name."""
    mine = " ".join(words(part) for part in parts)
    for state, code in STATES.items():
        if words(state) in mine or code in [part.strip() for part in parts]:
            continue
        if words(state) in words(option) or has_code(option, code):
            return True
    return False


def only_a_part(option: str, parts: list[str]) -> bool:
    """Whether the option is one part of the fact and nothing else."""
    name, said = words(option).strip(), [words(part).strip() for part in parts]
    return name in said or (name in US_NAMES and any(part in US_NAMES for part in said))


def location_option(render_input: FormatInput) -> str | None:
    """The one option naming the most specific part of "city, state, country"; no guess."""
    if not isinstance(render_input.value, str) or not render_input.options:
        return None
    if LOCATION_QUESTION.search(render_input.question) is None:
        return None
    parts = [part.strip() for part in render_input.value.split(",") if part.strip()]
    best: list[str] = []
    top = 0
    for option in render_input.options:
        if EXCLUDES.search(option) or names_other_state(option, parts):
            continue
        score = sum(
            2 ** (len(parts) - 1 - index)
            for index, part in enumerate(parts)
            if names_part(option, part)
        )
        if score > top:
            best, top = [option], score
        elif score == top and score > 0:
            best.append(option)
    if len(best) > 1:
        best = [option for option in best if only_a_part(option, parts)] or best
    return best[0] if len(best) == 1 else None


QUESTION = "What is your location?"
PLACE = "Denver, Colorado, United States"


def asked(options: tuple[str, ...], value: str = PLACE, question: str = QUESTION) -> FormatInput:
    return FormatInput(
        value=value, value_type="text", options=options, question=question, input_type="select"
    )


FORMAT = Format(
    name="location_area_choice",
    fact_keys=("address.location",),
    render=location_option,
    sites=("lever.co",),
    proven=False,
    examples=(
        (asked(("United States", "Canada", "Other")), "United States"),
        (asked(("US", "Canada")), "US"),
        (asked(("Outside the U.S.", "In the U.S.")), "In the U.S."),
        (asked(("Colorado", "United States")), "Colorado"),
        (asked(("Based in CO", "Based in the West")), "Based in CO"),
        (asked(("Denver, CO", "Denver, PA", "Remote")), "Denver, CO"),
        (asked(("Denver, PA", "Remote")), None),
        (asked(("West Virginia", "Virginia"), "Richmond, Virginia, United States"), "Virginia"),
        (asked(("San Mateo, CA", "New York, NY", "Other")), None),
        (asked(("USA", "United States")), None),
        (asked(("United States", "United States Minor Outlying Islands", "Virgin Islands, U.S."),
               "Atlanta, GA, USA"), "United States"),
        (asked(("United States",), question="Which office do you prefer?"), None),
        (asked(()), None),
    ),
)
