"""The later-graduating resume variant. Parked; see README.md in this package.

The canonical resume states December 2027. A few postings state a requirement
it does not meet ("must graduate after December 2027"); the only thing that
changes for those is the graduation date on the page.
"""

from __future__ import annotations

from typing import Any

CANONICAL_GRADUATION = "December 2027"
LATER_GRADUATION = "May 2028"


def with_graduation(resume: dict[str, Any], graduation: str) -> dict[str, Any]:
    """The v2 resume document with the primary school's graduation swapped."""
    education = [
        {**school, "graduation": graduation} if school.get("primary") else dict(school)
        for school in resume.get("education") or []
    ]
    return {**resume, "education": education}
