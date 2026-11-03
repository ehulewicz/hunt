"""Career-derived facts as of a date, and the career record's resume fetched through the API."""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from personal_os_worker.core.api import WorkerApi

# A co-op rotation is complete once its boundary is reached: spring on May 1,
# summer on August 1, fall on November 1 (decided 2026-10-08). Rotations are
# counted over each co-op entry's span on the career record (decided 2026-10-23).
ROTATION_BOUNDARIES = ((5, 1), (8, 1), (11, 1))
# Entries that count as having done an internship once they have ended.
INTERNSHIP_TYPES = frozenset({"internship", "coop"})
DAYS_PER_YEAR = 365.25


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _next_month(value: date) -> date:
    return date(value.year + value.month // 12, value.month % 12 + 1, 1)


def years_of_experience(entries: list[dict[str, Any]], today: date) -> int | None:
    """Whole years covered by any experience entry, overlaps counted once.

    An entry runs from its first month through the end of its last month, or
    through today when it has no end. Research and projects never count.
    """
    spans: list[tuple[date, date]] = []
    for entry in entries:
        if entry.get("section") != "experience":
            continue
        start = _date(entry.get("start_date"))
        if start is None:
            continue
        end = _date(entry.get("end_date"))
        stop = _next_month(end) if end is not None else today
        if stop > start:
            spans.append((start, stop))
    if not spans:
        return None
    covered = 0
    current_start, current_stop = None, None
    for start, stop in sorted(spans):
        if current_stop is None or start > current_stop:
            if current_start is not None and current_stop is not None:
                covered += (current_stop - current_start).days
            current_start, current_stop = start, stop
        else:
            current_stop = max(current_stop, stop)
    if current_start is not None and current_stop is not None:
        covered += (current_stop - current_start).days
    return int(covered // DAYS_PER_YEAR)


def completed_rotations(
    entries: list[dict[str, Any]], today: date, graduation: date | None = None
) -> int:
    """Rotation boundaries passed while a co-op entry ran, each counted once.

    A current entry runs to today; an ended one through the end of its last
    month, so a spring rotation ending in April completes on May 1. Nothing
    counts past graduation.
    """
    passed: set[date] = set()
    for entry in entries:
        if entry.get("section") != "experience" or entry.get("employment_type") != "coop":
            continue
        start = _date(entry.get("start_date"))
        if start is None:
            continue
        end = _date(entry.get("end_date"))
        until = min(_next_month(end), today) if end is not None else today
        if graduation is not None:
            until = min(until, graduation)
        passed.update(
            boundary
            for year in range(start.year, until.year + 1)
            for month, day in ROTATION_BOUNDARIES
            if start < (boundary := date(year, month, day)) <= until
        )
    return len(passed)


def prior_internship(entries: list[dict[str, Any]], today: date, rotations: int) -> bool:
    """An internship or co-op entry has ended, or a co-op has completed a rotation."""
    ended = any(
        entry.get("section") == "experience"
        and entry.get("employment_type") in INTERNSHIP_TYPES
        and (end := _date(entry.get("end_date"))) is not None
        and end <= today
        for entry in entries
    )
    return ended or rotations > 0


def _education_facts(education: dict[str, Any]) -> dict[str, Any]:
    """The facts the record's primary education row determines."""
    values: dict[str, Any] = {}
    values["education.school_name"] = education.get("school")
    values["education.degree_type"] = education.get("degree_type")
    values["education.field_of_study"] = education.get("field_of_study")
    graduation = _date(education.get("graduation_date"))
    values["education.graduation_date"] = graduation.isoformat() if graduation else None
    try:
        values["education.gpa"] = (
            float(education["gpa"]) if education.get("gpa") is not None else None
        )
    except (TypeError, ValueError):
        values["education.gpa"] = None
    values["education.coursework"] = [
        str(course).strip()
        for course in education.get("coursework") or []
        if str(course).strip()
    ] or None
    return values


def _high_school_facts(record: dict[str, Any]) -> dict[str, Any]:
    """The facts a high school row among the record's schools determines."""
    values: dict[str, Any] = {}
    # The record's non-primary schools: a high school row feeds these.
    schools = [row for row in record.get("schools") or [] if isinstance(row, dict)]
    high_school = next((row for row in schools if row.get("level") == "high_school"), None)
    if high_school is not None:
        values["education.high_school_name"] = high_school.get("school")
        location = str(high_school.get("location") or "").strip()
        values["education.high_school_city"] = location.split(",", 1)[0].strip() or None
        graduation = _date(high_school.get("graduation_date"))
        if graduation is not None:
            values["education.high_school_graduation_year"] = graduation.year
        else:
            match = re.search(r"\b(\d{4})\b", str(high_school.get("graduation") or ""))
            values["education.high_school_graduation_year"] = (
                int(match.group(1)) if match else None
            )
    return values


def _current_job_facts(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The employer, title and start date of the experience entry still running."""
    values: dict[str, Any] = {}
    current = next(
        (
            entry
            for entry in entries
            if entry.get("section") == "experience"
            and entry.get("start_date")
            and not entry.get("end_date")
            and (entry.get("employer") or entry.get("org"))
        ),
        None,
    )
    # A form gets an entry's `employer` and `job_title` where they are set, since a
    # heading need not be either; otherwise the heading's org and title.
    if current is not None:
        values["employment.current_employer"] = current.get("employer") or current.get("org")
        values["employment.current_title"] = current.get("job_title") or current.get("title")
        start = _date(current.get("start_date"))
        values["employment.current_start_date"] = start.isoformat() if start else None
    return values


def _organizations(entries: list[dict[str, Any]]) -> list[str]:
    """Every organization the entries name as an employer or lab, in order, once each."""
    organizations: list[str] = []
    for entry in entries:
        # Where you worked or researched: a project's "org" is an award, not an
        # employer, and an activity is not employment.
        if entry.get("section") in ("project", "activity"):
            continue
        # A lab's name is its research heading; its org spells out the acronym.
        names = [entry.get("org"), entry.get("employer")]
        if entry.get("section") == "research":
            names.append(entry.get("title"))
        for name in names:
            text = str(name or "").strip()
            if text and text not in organizations:
                organizations.append(text)
    return organizations


def _skills(record: dict[str, Any]) -> list[str]:
    """Every skill on the record, in order, once each."""
    skills: list[str] = []
    for group in record.get("skills") or []:
        items = group.get("items") if isinstance(group, dict) else None
        for item in items or []:
            name = str(item).strip()
            if name and name not in skills:
                skills.append(name)
    return skills


def career_facts(record: dict[str, Any] | None, today: date) -> dict[str, Any]:
    """The facts the career record determines, keyed like stored facts."""
    if not isinstance(record, dict):
        return {}
    values: dict[str, Any] = {}
    education = record.get("education")
    if isinstance(education, dict):
        values.update(_education_facts(education))
    values.update(_high_school_facts(record))
    entries = [entry for entry in record.get("entries") or [] if isinstance(entry, dict)]
    values["experience.years_professional"] = years_of_experience(entries, today)
    values["experience.years_full_time"] = years_of_experience(
        [
            entry
            for entry in entries
            if entry.get("employment_type") in {None, "founder"}
        ],
        today,
    )
    rotations = completed_rotations(
        entries, today, _date(values.get("education.graduation_date"))
    )
    values["coop.completed_rotations"] = rotations
    values["experience.prior_internship"] = prior_internship(entries, today, rotations)
    values.update(_current_job_facts(entries))
    values["employment.organizations"] = _organizations(entries) or None
    values["skills.all"] = _skills(record) or None
    return {key: value for key, value in values.items() if value not in (None, "")}


def repository_root() -> Path:
    configured = os.environ.get("PERSONAL_OS_REPOSITORY")
    if configured:
        return Path(configured).expanduser().resolve()
    # workers/src/personal_os_worker/jobs/facts/career.py -> repository root
    return Path(__file__).resolve().parents[5]


def transcript_pdf_path() -> Path | None:
    """The tracked transcript beside the resume, when there is one."""
    path = repository_root() / "artifacts" / "career" / "Transcript.pdf"
    return path if path.exists() else None


RESUME_PDF_NAME = "Alex_Example.pdf"


def download_resume_pdf(api: WorkerApi, directory: Path) -> Path:
    """The published resume PDF, written atomically as `directory/Alex_Example.pdf`."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / RESUME_PDF_NAME
    temporary = directory / f".{RESUME_PDF_NAME}.tmp"
    temporary.write_bytes(api.resume_pdf())
    temporary.replace(target)
    return target


def rejection(error: httpx.HTTPStatusError) -> str:
    """What the API said was wrong with a document, as one line."""
    try:
        detail = error.response.json().get("detail")
    except ValueError:
        return error.response.text.strip() or str(error)
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', [])[1:])}: {item.get('msg')}"
            for item in detail
            if isinstance(item, dict)
        )
    return str(detail)
