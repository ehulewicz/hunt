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
from decimal import Decimal, InvalidOperation
from typing import Any

from personal_os_worker.jobs.facts.derived import HOURS_PER_YEAR
from personal_os_worker.jobs.options import NUMBER, normalize_answer_question
from personal_os_worker.jobs.registry.formats import (
    Rendering,
    _hint,
    _number_text,
    _numeric,
)

ANNUAL = re.compile(r"\b(annual|annually|yearly|per year|a year|base salary)\b", re.IGNORECASE)

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

CURRENCIES = {
    "USD": ("usd", "us dollar", "u s dollar", "united states dollar", "american dollar"),
    "CAD": ("cad", "canadian dollar"),
    "EUR": ("eur", "euro"),
    "GBP": ("gbp", "british pound", "pound sterling", "pound"),
    "JPY": ("jpy", "yen"),
    "INR": ("inr", "rupee"),
    "AUD": ("aud", "australian dollar"),
    "AED": ("aed", "dirham", "emirati dirham", "uae dirham"),
    "MXN": ("mxn", "peso", "mexican peso"),
    "CHF": ("chf", "franc", "swiss franc"),
}
CURRENCY_WORD = re.compile(
    r"\b(dollar|euro|pound|yen|rupee|peso|franc|dirham)\b", re.IGNORECASE
)
# A whole option that is just a pay period: "Hourly", "Per year", "Salary".
PERIOD_WORD = re.compile(
    r"^\s*(hourly|weekly|biweekly|bi-weekly|semi[\s-]?monthly|monthly|yearly"
    r"|annually|annual|per hour|per week|per month|per year|salary)\s*$",
    re.IGNORECASE,
)
MONEY_PERIODS = {
    "hour": ("hourly", "per hour"),
    "week": ("weekly", "per week"),
    "biweek": ("biweekly", "bi weekly"),
    "month": ("monthly", "per month", "semi monthly", "semimonthly"),
    "year": ("yearly", "annually", "annual", "per year", "salary"),
}

def _money_menu(
    value: Any, field: dict[str, Any], question: str, options: list[str]
) -> Rendering | None:
    """A menu asking only the currency or only the period of a money fact."""
    if not isinstance(value, dict):
        return None
    normalized = [(option, normalize_answer_question(option)) for option in options]
    currencies = sum(CURRENCY_WORD.search(option) is not None for option in options)
    if "currency" in _hint(field, question) or currencies >= 2:
        code = str(value.get("currency") or "").strip().upper()
        aliases = CURRENCIES.get(code, (code.casefold(),)) if code else ()
        hits = [
            option
            for option, text in normalized
            if any(alias in text for alias in aliases)
        ]
        if len(hits) == 1:
            return Rendering(hits[0], "money_currency_option")
    if sum(PERIOD_WORD.match(option) is not None for option in options) >= 2:
        wanted = MONEY_PERIODS.get(
            str(value.get("period") or "").strip().casefold(), ()
        )
        hits = [
            option
            for option, text in normalized
            if any(word in text for word in wanted)
        ]
        if len(hits) == 1:
            return Rendering(hits[0], "money_period_option")
    return None


def _money_magnitude(options: list[str]) -> str | None:
    """"annual" when the options' numbers count in the thousands, else "hourly"."""
    numbers: list[float] = []
    for option in options:
        text = re.sub(r"[$,]|\busd\b", "", option.casefold())
        scale = 1000 if re.search(rf"{NUMBER}\s*k\b", text) else 1
        numbers.extend(float(number) * scale for number in re.findall(NUMBER, text))
    if not numbers:
        return None
    return "annual" if max(numbers) >= 1000 else "hourly"
