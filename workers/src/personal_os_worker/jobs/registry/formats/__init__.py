"""Formatters: one fact, rendered for the control in front of it (design §8).

A fact is stored once; "Fall 2027", "December 2027", "12/15/2027", and the
"Fall 2027" option of a term dropdown are renderings of one graduation date,
produced at fill time. A formatter is chosen from the fact's type, the
question's wording, the control, and — when the control offers options — which
rendering one option actually matches. Every rendering names its formatter, so a
value that fits no option is a `format` failure that can be attributed.

The families hold the renderings: `dates`, `money`, `phone`, `choices` (yes/no,
polarity, consent and source), and `places` (states, countries, schools, a city
with its state, the comparisons). This module holds what they share, the text
renderings (plain text, numbers, lists, notice-within), and the dispatch.
"""

from __future__ import annotations

import contextlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from personal_os_worker.jobs.focus import choose_focus, focus_text
from personal_os_worker.jobs.office import choose_offices
from personal_os_worker.jobs.office import parse as parse_place
from personal_os_worker.jobs.options import (
    choose_numeric_range,
    choose_option,
    normalize_answer_question,
)
from personal_os_worker.jobs.sites import option_match

MULTI = "\n"
# A fact whose value is the empty value of its type ("" or []) says the answer
# is none: write N/A or a truthful None option where the control permits it.
# BLANK identifies controls that cannot represent it; the audit reports a gap.
BLANK = "none_as_blank"
# Facts whose rendering against a typeahead nobody has opened is chosen against
# the live menu instead: the planner's chooser for each.
LIVE_MENU = {"consent.agrees": "consent", "preferences.application_source": "source"}


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


def _number_text(value: Any) -> str:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def _rounded_down(value: Any) -> str | None:
    """A fractional number's floor, as option text; None for a whole or non-number."""
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if number == number.to_integral_value():
        return None
    return str(math.floor(number))


def _year_floor(key: str, value: Any, field: dict[str, Any]) -> str | None:
    """A fractional experience total for a number input that cannot take a
    half: step unset (HTML's default 1) or whole rounds the years down. `any`
    or a fractional step keeps the half. Only the years fact floors — a
    measured decimal like GPA keeps its value."""
    if key != "experience.years_professional" or not _numeric(field):
        return None
    step = str(field.get("step") or "").strip()
    if step:
        try:
            if float(step) % 1 != 0:
                return None
        except ValueError:
            return None  # "any" or a non-number step takes decimals
    return _rounded_down(value)

def _multiple(field: dict[str, Any]) -> bool:
    if field.get("multiple") is True:
        return True
    try:
        return _input_type(field) == "checkbox" and int(field.get("group_size") or 1) > 1
    except (TypeError, ValueError):
        return False


def is_blank(value: Any) -> bool:
    """Whether a fact's value is the empty value of its type: the answer is none."""
    return (isinstance(value, str) and not value.strip()) or value == []


from personal_os_worker.jobs.registry.formats.choices import (  # noqa: E402
    _yes_no_pair,
    affirmative_option,
    polarity,
    source_option,
    yes_no_option,
)
from personal_os_worker.jobs.registry.formats.dates import (  # noqa: E402
    _date_candidates,
    choose_date_range,
    date_range,
)
from personal_os_worker.jobs.registry.formats.money import (  # noqa: E402
    _money,
    _money_magnitude,
    _money_menu,
)
from personal_os_worker.jobs.registry.formats.phone import (  # noqa: E402
    _phone_country,
    _phone_country_candidates,
    calling_code_menu,
    calling_code_option,
)
from personal_os_worker.jobs.registry.formats.places import (  # noqa: E402
    _city_with_state,
    _comparison,
    _state_option,
)

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


def _standing(
    key: str,
    value: Any,
    field: dict[str, Any],
    options: list[str],
    context: Mapping[str, Any] | None,
) -> Rendering | None:
    """The two standing rules as facts: agree to consent, and say how you came to a posting.

    A typeahead with no options read yet renders the fact as text, which the
    planner chooses against the live menu instead (`LIVE_MENU`).
    """
    referred = bool((context or {}).get("referral.referred"))
    if key == "consent.agrees":
        if value is not True:
            return None
        if options:
            chosen = affirmative_option(options)
            return Rendering(chosen, "affirmative_option") if chosen is not None else None
        return Rendering("Yes", "affirmative_option") if field.get("role") == "combobox" else None
    if options:
        chosen = source_option(options, referred=referred)
        return Rendering(chosen, "application_source") if chosen is not None else None
    text = "Employee referral" if referred else str(value).strip()
    return Rendering(text, "application_source") if text else None

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
        floored = _year_floor(key, value, field)
        if floored is not None:
            return [Rendering(floored, "number_rounded_down")]
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
        if key == "contact.phone_country":
            return _phone_country_candidates(value, field, question)
        return []
    text = str(value).strip()
    return [Rendering(text, "text_as_is")] if text else []


def _money_option(
    value: Any, field: dict[str, Any], question: str, options: list[str]
) -> Rendering | None:
    """A compensation fact among the options: its menu, else a range or option it matches."""
    menu = _money_menu(value, field, question, options)
    if menu is not None:
        return menu
    magnitude = _money_magnitude(options)
    for rendering in _money(value, field, question):
        if magnitude is not None and rendering.formatter != (
            "money_as_annual" if magnitude == "annual" else "money_as_hourly"
        ):
            continue
        digits = re.sub(r"[^\d.]", "", rendering.value)
        ranged = choose_numeric_range(options, digits) or choose_option(
            options, rendering.value
        )
        if ranged is not None:
            return Rendering(ranged, f"{rendering.formatter}_option")
    return None


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
    if key == "address.state":
        state = _state_option(options, str(value))
        if state is not None:
            return state
    compared = _comparison(key, value, question, options)
    if compared is not None:
        return compared
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
        # Whole-only menus take the value rounded down once nothing matches it.
        floored = _rounded_down(value)
        if floored is not None and option_match(options, _number_text(value)) is None:
            chosen = choose_numeric_range(options, floored) or choose_option(options, floored)
            if chosen is not None:
                return Rendering(chosen, "number_rounded_down")
    if key.startswith("compensation.") and value_type == "object":
        return _money_option(value, field, question, options)
    if value_type == "date":
        with contextlib.suppress(ValueError):
            when = date.fromisoformat(str(value))
            window = date_range(question)
            if window is not None and _yes_no_pair(options):
                chosen = yes_no_option(options, window[0] <= when <= window[1])
                if chosen is not None:
                    return Rendering(chosen, "date_in_range_option")
            ranged = choose_date_range(options, when)
            if ranged is not None:
                return Rendering(ranged, "date_as_range_option")
    candidates = _text_candidates(key, value, value_type, field, question)
    if prefer:
        candidates.sort(key=lambda rendering: rendering.formatter != prefer)
    for rendering in candidates:
        chosen = choose_option(options, rendering.value)
        if chosen is not None:
            return Rendering(chosen, f"{rendering.formatter}_option")
    return None


def _none_for(field: dict[str, Any]) -> Rendering:
    """How this control says the answer is none: a None option, N/A, or BLANK."""
    options = _options(field)
    for option in options:
        if normalize_answer_question(option) in {"none", "n a", "not applicable"}:
            return Rendering(option, "none_as_option")
    if (not options and field.get("tag") in {"input", "textarea"}
            and _input_type(field) in {"", "text", "search"}
            and field.get("role") != "combobox"):
        return Rendering("N/A", "none_as_na")
    return Rendering("", BLANK)


def _engineering_focus(
    value: list[Any],
    field: dict[str, Any],
    question: str,
    options: list[str],
    *,
    seed: str,
    context: Mapping[str, Any] | None,
) -> Rendering | None:
    """The engineering focus: the options the interests choose, else focus text."""
    title = str((context or {}).get("posting.title") or "")
    interests = [str(item) for item in value if str(item).strip()]
    if options:
        focused = choose_focus(
            options,
            interests,
            title=title,
            seed=seed,
            question=question,
            multiple=_multiple(field),
        )
        return Rendering(MULTI.join(focused), "focus_choice") if focused else None
    text = focus_text(interests, title=title, seed=seed)
    return Rendering(text, "focus_text") if text else None


def _render(
    key: str,
    value: Any,
    value_type: str,
    *,
    field: dict[str, Any],
    question: str,
    prefer: str | None = None,
    seed: str = "",
    context: Mapping[str, Any] | None = None,
) -> Rendering | None:
    """The fact's value as this control needs it, or None when nothing fits.

    `context` is the run's other facts, for the few renderings that need one
    (a city in a typeahead goes in with its state, a country in a menu of
    calling codes takes the phone's code). A value that is the empty value of
    its type renders as N/A, a None option, or a gap for an incompatible control.
    """
    if value is None:
        return None
    if is_blank(value):
        return _none_for(field)
    options = _options(field)
    if key in LIVE_MENU:
        return _standing(key, value, field, options, context)
    if key == "preferences.engineering_focus" and isinstance(value, list) and value:
        return _engineering_focus(value, field, question, options, seed=seed, context=context)
    phone = _phone_country(key, value, context)
    if phone is not None and calling_code_menu(options):
        chosen = calling_code_option(options, *phone)
        return Rendering(chosen, "calling_code_option") if chosen is not None else None
    within = _notice_within(key, value, question)
    if within is not None:
        if options:
            chosen = yes_no_option(options, within)
            return Rendering(chosen, "notice_within_option") if chosen else None
        return Rendering("Yes" if within else "No", "notice_within")
    with_state = _city_with_state(key, value, context)
    if options:
        if with_state is not None:
            # Five Atlantas: the option naming the same city and state.
            wanted = parse_place(with_state)
            same = [
                option for option in options
                if (place := parse_place(option)).city
                and wanted.city
                and place.city.casefold() == wanted.city.casefold()
                and place.state == wanted.state
            ]
            if len(same) == 1:
                return Rendering(same[0], "city_with_state_option")
        return _choose_among(
            key, value, value_type, field, question, options, prefer=prefer, seed=seed
        )
    if with_state is not None and str(field.get("role") or "") == "combobox":
        return Rendering(with_state, "city_with_state")
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


def render(
    key: str,
    value: Any,
    value_type: str,
    *,
    field: dict[str, Any],
    question: str,
    prefer: str | None = None,
    seed: str = "",
    context: Mapping[str, Any] | None = None,
    site: str = "",
) -> Rendering | None:
    """The built-in rendering, else the site's learned formats, or None."""
    built_in = _render(
        key, value, value_type, field=field, question=question, prefer=prefer,
        seed=seed, context=context,
    )
    if built_in is not None or not site:
        return built_in
    from personal_os_worker.jobs import registry
    from personal_os_worker.jobs.registry.api import FormatInput

    for learned_format in registry.learned("formats", site):
        if key not in learned_format.fact_keys:
            continue
        written = learned_format.render(
            FormatInput(
                value=value,
                value_type=value_type,
                options=tuple(_options(field)),
                question=question,
                input_type=_input_type(field),
            )
        )
        if written is not None:
            module = str(getattr(learned_format, "__module__", "")).rsplit(".", 1)[-1]
            registry.report(learned_format, True)
            return Rendering(written, f"learned:{module}")
    return None


def renderings(
    key: str, value: Any, value_type: str, *, field: dict[str, Any], question: str
) -> list[Rendering]:
    """Every distinct way this fact can be written, the wording's choice first.

    Repair tries each against a live control whose options only appear when
    typed into, where planning could not know which one the menu would accept.
    """
    if value is None:
        return []
    if key == "preferences.engineering_focus" and isinstance(value, list):
        return [
            Rendering(str(item), "focus_item") for item in value if str(item).strip()
        ]
    found: list[Rendering] = []
    for rendering in _text_candidates(key, value, value_type, field, question):
        if rendering.value not in {item.value for item in found}:
            found.append(rendering)
    return found
