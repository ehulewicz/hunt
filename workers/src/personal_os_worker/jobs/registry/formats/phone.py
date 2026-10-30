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
from typing import Any

from personal_os_worker.jobs.options import CALLING_CODE, choose_option
from personal_os_worker.jobs.registry.formats import Rendering, _hint, _words


def _phone_country(
    key: str, value: Any, context: Mapping[str, Any] | None
) -> tuple[str, str] | None:
    """(country, calling code digits) when this fact names the phone's country.

    `contact.phone_country` does; so does the country of residence when it is
    the same country, which is how a "Country" menu of calling codes beside a
    phone box is answered.
    """
    if key not in {"contact.phone_country", "address.country"}:
        return None
    phone = value if key == "contact.phone_country" else (context or {}).get(
        "contact.phone_country"
    )
    if not isinstance(phone, dict):
        return None
    country = str(phone.get("country") or "").strip()
    code = re.sub(r"\D", "", str(phone.get("calling_code") or ""))
    if not country or not code:
        return None
    if key == "address.country" and _words(str(value)) != _words(country):
        return None
    return country, code


def calling_code_menu(options: list[str]) -> bool:
    """Whether every option is a calling code, alone or beside its country's name."""
    return len(options) >= 2 and all(CALLING_CODE.search(option) for option in options)


def calling_code_option(options: list[str], country: str, code: str) -> str | None:
    """The option with this calling code; of several (+1 is shared), the one naming the country."""
    coded = [
        option for option in options
        if (found := CALLING_CODE.search(option)) is not None and found.group(1) == code
    ]
    if len(coded) == 1:
        return coded[0]
    names = {" ".join(CALLING_CODE.sub(" ", option).split()): option for option in coded}
    chosen = choose_option(list(names), country)
    return names[chosen] if chosen is not None else None


def _phone_country_candidates(
    value: Any, field: dict[str, Any], question: str
) -> list[Rendering]:
    """The phone's country as its name or its calling code, the wording's first.

    A box asking for a code gets "+1"; a typeahead is searched by name, since
    many countries share a code.
    """
    if not isinstance(value, dict):
        return []
    country = str(value.get("country") or "").strip()
    code = re.sub(r"\D", "", str(value.get("calling_code") or ""))
    found = ([Rendering(country, "country_name")] if country else []) + (
        [Rendering(f"+{code}", "calling_code")] if code else []
    )
    asks_code = re.search(r"\b(code|dial\w*)\b", _hint(field, question)) is not None
    if asks_code and str(field.get("role") or "") != "combobox":
        found.reverse()
    return found
