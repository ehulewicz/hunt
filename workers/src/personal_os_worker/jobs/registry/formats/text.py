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

WITHIN = re.compile(
    r"\bwithin\s+(\d+|one|two|three|four|five|six|seven|eight)\s+(weeks?|days?)\b",
    re.IGNORECASE,
)
SMALL_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
}


def _notice_within(key: str, value: Any, question: str) -> bool | None:
    """"Can you start within 3 weeks?" answered from the notice period in weeks."""
    asked = WITHIN.search(question) if key == "availability.notice_period_weeks" else None
    if asked is None:
        return None
    try:
        weeks = float(value)
    except (TypeError, ValueError):
        return None
    count = asked.group(1).casefold()
    limit = float(SMALL_NUMBERS.get(count) or count)
    if asked.group(2).casefold().startswith("day"):
        limit /= 7
    return weeks <= limit
