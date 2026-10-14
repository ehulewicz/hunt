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
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from personal_os_worker.jobs.adapters import is_non_answer_option
from personal_os_worker.jobs.facts.derived import HOURS_PER_YEAR, graduation_term
from personal_os_worker.jobs.office import choose_offices
from personal_os_worker.jobs.options import choose_numeric_range, choose_option

MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
# Several values chosen in one control (a checkbox group, a multi-select) are
# joined with a newline, which no option text contains, rather than a comma,
# which "New York, NY" does.
MULTI = "\n"
ANNUAL = re.compile(r"\b(annual|annually|yearly|per year|a year|base salary)\b", re.IGNORECASE)
NEGATIVE = re.compile(r"\b(no|not|don t|do not|never|none|neither)\b")
AFFIRMATIVE = re.compile(r"^(yes|true|y)$")
NEGATIVE_WORD = re.compile(r"^(no|false|n)$")


@dataclass(frozen=True, slots=True)
class Rendering:
    value: str
    formatter: str


def _words(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _options(field: dict[str, Any]) -> list[str]:
    return [str(option) for option in field.get("options") or [] if str(option).strip()]


def _input_type(field: dict[str, Any]) -> str:
    return str(field.get("type") or "").casefold()


def _numeric(field: dict[str, Any]) -> bool:
    return _input_type(field) in {"number", "range"}


def _hint(field: dict[str, Any], question: str) -> str:
    return " ".join(
        str(part or "") for part in (question, field.get("placeholder"), field.get("label"))
    ).casefold()


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
    if answer:
        hits = [option for option in eligible if not NEGATIVE.search(_words(option))]
    else:
        hits = [option for option in eligible if NEGATIVE.search(_words(option))]
    return hits[0] if len(hits) == 1 else None


def _number_text(value: Any) -> str:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def _month_year(value: date) -> str:
    return f"{MONTHS[value.month - 1]} {value.year}"


def _date_candidates(value: date, field: dict[str, Any], question: str) -> list[Rendering]:
    """Every rendering of a date, the wording's first."""
    every = {
        "date_as_month_year": _month_year(value),
        "date_as_term": f"{graduation_term(value)} {value.year}",
        "date_as_month_name": MONTHS[value.month - 1],
        "date_as_year": str(value.year),
        "date_as_iso": value.isoformat(),
        "date_as_us": f"{value.month:02d}/{value.day:02d}/{value.year}",
        "date_as_month_input": f"{value.year}-{value.month:02d}",
    }
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


def _money(value: Any, field: dict[str, Any], question: str) -> list[Rendering]:
    if not isinstance(value, dict):
        return []
    try:
        amount = Decimal(str(value.get("amount")))
    except InvalidOperation:
        return []
    hourly = amount if value.get("period") == "hour" else amount / HOURS_PER_YEAR
    annual = amount if value.get("period") == "year" else amount * HOURS_PER_YEAR
    hourly_text = _number_text(hourly)
    annual_number = _number_text(annual.quantize(Decimal("1")))
    numeric = _numeric(field)
    hourly_rendering = Rendering(
        hourly_text if numeric else f"${hourly_text}/hour", "money_as_hourly"
    )
    annual_rendering = Rendering(
        annual_number if numeric else f"${int(annual):,} USD", "money_as_annual"
    )
    if ANNUAL.search(_hint(field, question)):
        return [annual_rendering, hourly_rendering]
    return [hourly_rendering, annual_rendering]


def _text_candidates(
    key: str, value: Any, value_type: str, field: dict[str, Any], question: str
) -> list[Rendering]:
    if value_type == "date":
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError:
            return []
        return _date_candidates(parsed, field, question)
    if value_type == "boolean":
        return [Rendering("Yes" if value else "No", "boolean_as_yes_no")]
    if value_type == "number":
        return [Rendering(_number_text(value), "number_as_text")]
    if value_type == "list":
        items = [str(item) for item in value or [] if str(item).strip()]
        if not items:
            return []
        joined = Rendering(", ".join(items), "list_as_joined")
        return [Rendering(items[0], "list_as_single"), joined] if len(items) == 1 else [joined]
    if value_type == "object":
        if key.startswith("compensation."):
            return _money(value, field, question)
        return []
    text = str(value).strip()
    return [Rendering(text, "text_as_is")] if text else []


def _choose_among(
    key: str,
    value: Any,
    value_type: str,
    field: dict[str, Any],
    question: str,
    options: list[str],
    *,
    prefer: str | None,
    seed: str,
) -> Rendering | None:
    if prefer == "citizenship_option" and value is True:
        hits = [
            option
            for option in options
            if re.search(r"\b(nationality|citizen(?:ship)?)\b", option, re.IGNORECASE)
            and not re.search(r"\b(not|no)\b", option, re.IGNORECASE)
        ]
        if len(hits) == 1:
            return Rendering(hits[0], "citizenship_option")
    if key == "preferences.location_strategy":
        offices = choose_offices(
            options, value, seed=seed, multiple=_multiple(field), question=question
        )
        return Rendering(MULTI.join(offices), "office_choice") if offices else None
    answer = polarity(value) if value_type in {"boolean", "text", "choice"} else None
    if answer is not None:
        chosen = yes_no_option(options, answer)
        if chosen is not None:
            return Rendering(chosen, "boolean_as_option")
    if value_type == "list" and _multiple(field):
        items = [str(item) for item in value or []]
        picked = [
            option for option in options if any(choose_option([option], item) for item in items)
        ]
        if picked:
            return Rendering(MULTI.join(picked), "list_as_options")
        return None
    if value_type == "number":
        ranged = choose_numeric_range(options, _number_text(value))
        if ranged is not None:
            return Rendering(ranged, "number_as_range_option")
    if key.startswith("compensation.") and value_type == "object":
        for rendering in _money(value, field, question):
            digits = re.sub(r"[^\d.]", "", rendering.value)
            ranged = choose_numeric_range(options, digits) or choose_option(
                options, rendering.value
            )
            if ranged is not None:
                return Rendering(ranged, f"{rendering.formatter}_option")
        return None
    candidates = _text_candidates(key, value, value_type, field, question)
    if prefer:
        candidates.sort(key=lambda rendering: rendering.formatter != prefer)
    for rendering in candidates:
        chosen = choose_option(options, rendering.value)
        if chosen is not None:
            return Rendering(chosen, f"{rendering.formatter}_option")
    return None


def _multiple(field: dict[str, Any]) -> bool:
    if field.get("multiple") is True:
        return True
    try:
        return _input_type(field) == "checkbox" and int(field.get("group_size") or 1) > 1
    except (TypeError, ValueError):
        return False


def render(
    key: str,
    value: Any,
    value_type: str,
    *,
    field: dict[str, Any],
    question: str,
    prefer: str | None = None,
    seed: str = "",
) -> Rendering | None:
    """The fact's value as this control needs it, or None when nothing fits."""
    if value is None:
        return None
    options = _options(field)
    if options:
        return _choose_among(
            key, value, value_type, field, question, options, prefer=prefer, seed=seed
        )
    candidates = _text_candidates(key, value, value_type, field, question)
    if prefer:
        candidates.sort(key=lambda rendering: rendering.formatter != prefer)
    if not candidates:
        return None
    rendering = candidates[0]
    if _numeric(field) and not re.fullmatch(r"-?\d+(?:\.\d+)?", rendering.value):
        digits = re.sub(r"[^\d.]", "", rendering.value)
        if not digits:
            return None
        rendering = Rendering(digits, rendering.formatter)
    limit = field.get("max_length")
    if isinstance(limit, int) and limit > 0 and len(rendering.value) > limit:
        return None
    return rendering
