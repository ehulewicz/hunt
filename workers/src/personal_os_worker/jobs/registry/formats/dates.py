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
from datetime import date
from typing import Any

from personal_os_worker.jobs.facts.derived import graduation_term
from personal_os_worker.jobs.registry.formats import Rendering, _hint, _input_type

MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

def _month_year(value: date) -> str:
    return f"{MONTHS[value.month - 1]} {value.year}"

def _date_candidates(value: date, field: dict[str, Any], question: str) -> list[Rendering]:
    """Every rendering of a date, the wording's first."""
    every = {
        "date_as_month_year": _month_year(value),
        "date_as_term": f"{graduation_term(value)} {value.year}",
    }
    if value.month >= 11:
        # A November or December date is also a Winter.
        every["date_as_winter_term"] = f"Winter {value.year}"
    every.update({
        "date_as_month_name": MONTHS[value.month - 1],
        "date_as_year": str(value.year),
        "date_as_iso": value.isoformat(),
        "date_as_us": f"{value.month:02d}/{value.day:02d}/{value.year}",
        "date_as_month_input": f"{value.year}-{value.month:02d}",
    })
    hint = _hint(field, question)
    kind = _input_type(field)
    if kind == "date":
        first = "date_as_iso"
    elif kind == "month":
        first = "date_as_month_input"
    elif re.search(r"\b(term|semester)\b", hint):
        first = "date_as_term"
    elif re.search(r"mm\s*/\s*dd|dd\s*/\s*mm", hint):
        first = "date_as_us"
    elif "yyyy-mm-dd" in hint:
        first = "date_as_iso"
    elif re.search(r"\bmonth\b", hint) and not re.search(r"\byear\b", hint):
        first = "date_as_month_name"
    elif re.search(r"\byear\b", hint) and not re.search(r"\bmonth\b", hint):
        first = "date_as_year"
    else:
        first = "date_as_month_year"
    order = [first, *(name for name in every if name != first)]
    return [Rendering(every[name], name) for name in order]

_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{4})"


def _month_start(month: str, year: str) -> date:
    return date(int(year), [name[:3].casefold() for name in MONTHS].index(month[:3]) + 1, 1)


def _month_end(month: str, year: str) -> date:
    start = _month_start(month, year)
    following = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return date.fromordinal(following.toordinal() - 1)

_MONTH_NAME = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"

def date_range(text: str) -> tuple[date, date] | None:
    """The month range a text states, or None when it states none.

    "Between June 2027 and June 2028", "Jan - April 2027" (one year shared
    by both ends, joined by a hyphen or a dash), "before June 2028",
    "after May 2027".
    """
    text = text.casefold()
    months = re.findall(_MONTH, text)
    if re.match(r"^\s*(before|prior to|earlier than)\b", text) and len(months) == 1:
        return date.min, date.fromordinal(_month_start(*months[0]).toordinal() - 1)
    if re.match(r"^\s*(after|later than)\b", text) and len(months) == 1:
        return date.fromordinal(_month_end(*months[0]).toordinal() + 1), date.max
    if len(months) == 2 and re.search(r"\b(between|and|to|through)\b|[-\u2013\u2014]", text):
        return _month_start(*months[0]), _month_end(*months[1])
    shared = re.match(
        rf"^\s*{_MONTH_NAME}\b\.?\s*[-\u2013\u2014]+\s*{_MONTH_NAME}\b\.?\s+(\d{{4}})\s*$",
        text,
    )
    if shared is not None:
        first, second, year = shared.groups()
        if _month_start(first, year) <= _month_start(second, year):
            return _month_start(first, year), _month_end(second, year)
    return None


def choose_date_range(options: list[str], when: date) -> str | None:
    """The one option whose month range holds the date ("Sept - Dec 2027" for
    2027-12-01); None when none or several do."""
    hits = [
        option
        for option in options
        if (window := date_range(option)) is not None
        and window[0] <= when <= window[1]
    ]
    return hits[0] if len(hits) == 1 else None
