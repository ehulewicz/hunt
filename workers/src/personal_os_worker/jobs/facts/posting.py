"""Posting facts: what the posting being filled says about you and it."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

from personal_os_worker.jobs.facts.career import _date


def worked_for(organizations: Any, company: str) -> bool | None:
    """Whether the posting's company is one you worked or researched at.

    Matches either way round as whole words ("Example University" in "Example Lab @
    Example University"), ignoring case; None when there is nothing to compare.
    """
    needle = " ".join(company.casefold().split())
    if len(needle) < 3 or not isinstance(organizations, list) or not organizations:
        return None
    names = [" ".join(str(name).casefold().split()) for name in organizations]

    def contains(outer: str, inner: str) -> bool:
        return len(inner) >= 3 and re.search(rf"(?<!\w){re.escape(inner)}(?!\w)", outer) is not None

    return any(contains(name, needle) or contains(needle, name) for name in names if name)


# An application that went in, whatever happened after.
SUBMITTED_STATUSES = frozenset({"applied", "oa", "oa_completed", "interview", "offer", "rejected"})


# When a term's internship usually starts (month, day), and how long one runs
# (decided 2026-10-18: aim for twelve-week ranges).
TERM_STARTS = {"winter": (1, 4), "spring": (1, 11), "summer": (5, 18), "fall": (8, 31)}
INTERNSHIP_WEEKS = 12
TERM = re.compile(
    r"\b(winter|spring|summer|fall|autumn)\b(?:\s*[/&-]\s*(?:winter|spring|summer|fall|autumn))*"
    r"\s*['\u2019]?(\d{4}|\d{2})\b",
    re.IGNORECASE,
)


def internship_window(
    title: str, today: date, notice_weeks: Any = 2
) -> tuple[date, date, str | None]:
    """(start, end, term) for this posting.

    You can start two weeks after accepting (your notice period); a posting
    that names its term ("Summer 2027") starts no earlier than that term does.
    The window runs twelve weeks from the start.
    """
    try:
        weeks = max(0, int(notice_weeks))
    except (TypeError, ValueError):
        weeks = 2
    earliest = today + timedelta(weeks=weeks)
    found = TERM.search(title or "")
    if found is None:
        return earliest, earliest + timedelta(weeks=INTERNSHIP_WEEKS), None
    season = "fall" if found.group(1).casefold() == "autumn" else found.group(1).casefold()
    year = int(found.group(2))
    year = year + 2000 if year < 100 else year
    month, day = TERM_STARTS[season]
    start = max(date(year, month, day), earliest)
    return start, start + timedelta(weeks=INTERNSHIP_WEEKS), f"{season.title()} {year}"


def overlaps_final_term(start: date, end: date, graduation: date | None) -> bool:
    """Whether the window runs into the last semester before graduating, which you
    attend (decided 2026-10-18: avoid Fall 2027)."""
    if graduation is None:
        return False
    final_term_start = date(graduation.year, 8, 15) if graduation.month >= 9 else date(
        graduation.year, 1, 5
    )
    return start < graduation and end > final_term_start


def _long_date(value: date) -> str:
    return f"{value:%B} {value.day}, {value.year}"


def posting_facts(
    facts: dict[str, Any],
    company: str,
    prior_applications: Iterable[dict[str, Any]] | None = None,
    *,
    title: str = "",
    today: date | None = None,
    referral: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Facts about you and this posting: its company, its term, how you came to it."""
    found: dict[str, Any] = {}
    day = today or date.today()
    if title.strip():
        found["posting.title"] = title.strip()
    start, end, term = internship_window(title, day, facts.get("availability.notice_period_weeks"))
    found["availability.earliest_start_date"] = start.isoformat()
    found["availability.internship_dates"] = f"{_long_date(start)} \u2013 {_long_date(end)}"
    found["availability.conflicts_with_school"] = overlaps_final_term(
        start, end, _date(facts.get("education.graduation_date"))
    )
    graduation = _date(facts.get("education.graduation_date"))
    if graduation is not None:
        found["education.enrolled_through_internship"] = graduation > end
    if term is not None:
        found["availability.term"] = term
    # A referral is about this application, not about you: `apply --referred`.
    referred = referral or {}
    found["referral.referred"] = bool(referred.get("referred"))
    if referred.get("name"):
        found["referral.referrer_name"] = str(referred["name"])
    known = worked_for(facts.get("employment.organizations"), company)
    if known is not None:
        found["employment.worked_for"] = known
    if prior_applications is not None:
        found["employment.applied_before"] = any(
            str(item.get("status")) in SUBMITTED_STATUSES for item in prior_applications
        )
    return found
