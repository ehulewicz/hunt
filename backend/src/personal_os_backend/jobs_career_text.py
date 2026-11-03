"""The resume's spelling of record values: months, titles, locations, decimals, and the header."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

# The authored range separator in the resume ("May 2025 \u2013 Present").
EN_DASH = "–"  # noqa: RUF001 - the authored en dash
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
# The rendered title carries the employment type as a suffix: "Middleware
# Engineer (Co-op)" is title "Middleware Engineer" plus employment type coop.
TITLE_SUFFIXES = {
    "coop": "Co-op",
    "contract": "Contract",
    "internship": "Intern",
    "part_time": "Part-time",
}
TITLE_SUFFIX = re.compile(
    r"^(?P<title>.*?)\s*\((?P<kind>co-?op|contract|intern(?:ship)?|part-?time)\)\s*$",
    re.IGNORECASE,
)
# A US location is rendered "City, ST"; the country is spelled as the address facts spell it.
UNITED_STATES = "United States"
US_STATE = re.compile(r"^[A-Z]{2}$")
# A range separator is the authored en dash, or a plain hyphen typed in an editor.
RANGE = re.compile(r"^(?P<start>.+?)\s+[–-]\s+(?P<end>.+)$")  # noqa: RUF001


class CareerError(ValueError):
    """A document that cannot become the career record, with the reason."""


def parse_month(text: str) -> date:
    """"May 2025" -> 2025-05-01; ValueError for anything else."""
    parts = text.split()
    if len(parts) == 2 and parts[0].capitalize() in MONTHS and parts[1].isdigit():
        return date(int(parts[1]), MONTHS.index(parts[0].capitalize()) + 1, 1)
    raise CareerError(f"{text!r} is not a month and year such as 'May 2025'")


def render_month(value: date) -> str:
    return f"{MONTHS[value.month - 1]} {value.year}"


def parse_range(text: str) -> tuple[date | None, date | None]:
    """A rendered date line as (start, end); end None with a start means Present.

    A single month is an end date alone: a project shipped in "June 2026".
    """
    value = text.strip()
    if not value:
        return None, None
    matched = RANGE.match(value)
    if matched is None:
        return None, parse_month(value)
    start = parse_month(matched["start"].strip())
    end_text = matched["end"].strip()
    if end_text.casefold() == "present":
        return start, None
    end = parse_month(end_text)
    if end < start:
        raise CareerError(f"{text!r} ends before it starts")
    return start, end


def render_range(start: date | None, end: date | None) -> str:
    if start is not None and end is not None:
        return f"{render_month(start)} {EN_DASH} {render_month(end)}"
    if start is not None:
        return f"{render_month(start)} {EN_DASH} Present"
    if end is not None:
        return render_month(end)
    return ""


def split_title(section: str, title: str) -> tuple[str, str | None]:
    """The stored title and employment type of a rendered heading."""
    if section != "experience":
        return title.strip(), None
    matched = TITLE_SUFFIX.match(title.strip())
    if matched is not None:
        kind = matched["kind"].casefold().replace("-", "")
        employment = {"coop": "coop", "contract": "contract", "parttime": "part_time"}.get(
            kind, "internship"
        )
        return matched["title"].strip(), employment
    return title.strip(), "founder" if "founder" in title.casefold() else "full_time"


def parse_location(text: str) -> tuple[str | None, str | None, str | None]:
    """A rendered location as (city, region, country).

    "Atlanta, GA" is a US city and its state; otherwise the last part is the
    country and a middle part the region: "Metz, France", "Toronto, ON, Canada".
    """
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        return None, None, None
    if len(parts) == 1:
        return parts[0], None, None
    if len(parts) == 2 and US_STATE.match(parts[1]):
        return parts[0], parts[1], UNITED_STATES
    return parts[0], ", ".join(parts[1:-1]) or None, parts[-1]


def render_location(city: str | None, region: str | None, country: str | None) -> str:
    """The resume's spelling: "Atlanta, GA" at home, "Metz, France" abroad."""
    if country == UNITED_STATES and region:
        country = None
    return ", ".join(part for part in (city, region, country) if part)


def render_title(title: str, employment_type: str | None) -> str:
    suffix = TITLE_SUFFIXES.get(employment_type or "")
    return f"{title} ({suffix})" if suffix else title


def render_decimal(value: Any) -> str:
    return "" if value is None else f"{Decimal(str(value)):.2f}"


def parse_decimal(text: str, field: str) -> Decimal | None:
    if not text.strip():
        return None
    try:
        return Decimal(text.strip())
    except InvalidOperation:
        raise CareerError(f"education.{field} must be a number, not {text!r}") from None


def contact_from_facts(values: dict[str, Any]) -> dict[str, str]:
    """The resume header, rendered from facts the way the resume has always shown them."""
    phone = str(values.get("contact.phone") or "").strip()
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        phone = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return {
        "name": str(values.get("identity.legal_name") or "").strip(),
        "phone": phone,
        "email": str(values.get("contact.email") or "").strip(),
        "linkedin": str(values.get("contact.linkedin_url") or "").strip().rstrip("/"),
        "github": str(values.get("contact.github_url") or "").strip().rstrip("/"),
        "citizenship": "US Citizen" if values.get("work_auth.us_citizen") is True else "",
    }
