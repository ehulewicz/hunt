"""Derived facts: computed from base facts and today's date, never stored.

A derived fact goes stale the moment it is written down, so nothing here is
persisted. Each one is a pure function of the base facts (keyed by their dotted
names) and the date it is asked on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

# A co-op rotation is complete once its boundary is reached: spring on May 1,
# summer on August 1, fall on November 1 (decided 2026-10-08).
ROTATION_BOUNDARIES = ((5, 1), (8, 1), (11, 1))
HOURS_PER_YEAR = 2080


@dataclass(frozen=True, slots=True)
class DerivedFact:
    key: str
    label: str
    reads: tuple[str, ...]


DERIVED_FACTS = (
    DerivedFact(
        "coop.completed_rotations",
        "Completed co-op rotations as of today",
        ("coop.anchor", "education.graduation_date"),
    ),
    DerivedFact(
        "education.graduation_term", "Graduation term", ("education.graduation_date",)
    ),
    DerivedFact(
        "education.currently_enrolled",
        "Currently enrolled as a student",
        ("education.graduation_date",),
    ),
    DerivedFact(
        "compensation.minimum_annual", "Minimum annual compensation", ("compensation.minimum",)
    ),
    DerivedFact(
        "experience.prior_internship",
        "Has completed an internship or co-op",
        ("coop.anchor", "education.graduation_date"),
    ),
)


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def completed_rotations(anchor: Any, graduation: date | None, today: date) -> int | None:
    """The anchor's count plus every boundary after it, up to today or graduation."""
    if not isinstance(anchor, dict):
        return None
    count = anchor.get("count")
    as_of = _date(anchor.get("as_of"))
    if isinstance(count, bool) or not isinstance(count, int) or as_of is None:
        return None
    until = min(today, graduation) if graduation is not None else today
    passed = sum(
        1
        for year in range(as_of.year, until.year + 1)
        for month, day in ROTATION_BOUNDARIES
        if as_of < date(year, month, day) <= until
    )
    return count + passed


def graduation_term(graduation: date) -> str:
    """The academic term a graduation date ends: December is Fall."""
    if graduation.month <= 5:
        return "Spring"
    if graduation.month <= 8:
        return "Summer"
    return "Fall"


def annual_minimum(minimum: Any) -> int | float | None:
    if not isinstance(minimum, dict):
        return None
    amount = minimum.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int | float):
        return None
    if minimum.get("period") == "hour":
        return amount * HOURS_PER_YEAR
    if minimum.get("period") == "year":
        return amount
    return None


def derive(facts: dict[str, Any], today: date) -> dict[str, Any]:
    """Every derived fact whose inputs are present, keyed by its dotted name."""
    graduation = _date(facts.get("education.graduation_date"))
    rotations = completed_rotations(facts.get("coop.anchor"), graduation, today)
    values: dict[str, Any] = {
        "coop.completed_rotations": rotations,
        "education.graduation_term": graduation_term(graduation) if graduation else None,
        "education.currently_enrolled": today < graduation if graduation else None,
        "compensation.minimum_annual": annual_minimum(facts.get("compensation.minimum")),
        "experience.prior_internship": rotations > 0 if rotations is not None else None,
    }
    return {key: value for key, value in values.items() if value is not None}
