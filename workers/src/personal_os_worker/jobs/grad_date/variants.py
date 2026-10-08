"""The later-graduating resume variant. Parked; see README.md in this package.

The canonical resume states December 2027. A few postings state a requirement
it does not meet ("must graduate after December 2027"); the only thing that
changes for those is the graduation date on the page.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from personal_os_worker.documents.resume import build_resume, canonical_resume_path, load_resume

CANONICAL_GRADUATION = "December 2027"
LATER_GRADUATION = "May 2028"


def with_graduation(resume: dict[str, Any], graduation: str) -> dict[str, Any]:
    return {**resume, "education": {**resume["education"], "graduation": graduation}}


def later_graduation_resume_pdf() -> Path:
    """A resume compiled with `LATER_GRADUATION`, for a posting that requires it.

    Cached under the state directory; rebuilt whenever the canonical source is
    newer than the cached copy, the same staleness rule `ensure_resume` uses
    for the canonical PDF.
    """
    from personal_os_worker.core.service import state_dir

    source = canonical_resume_path()
    cached = state_dir() / "resume-variants" / "later-graduation.pdf"
    if cached.exists() and cached.stat().st_mtime >= source.stat().st_mtime:
        return cached
    resume = with_graduation(load_resume(source), LATER_GRADUATION)
    cached.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="resume-variant-", dir=cached.parent) as raw:
        temporary_source = Path(raw) / "main.json"
        temporary_source.write_text(json.dumps(resume))
        build_resume(temporary_source, cached)
    return cached
