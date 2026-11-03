"""The resume document and the rows behind it: models, writing rules, and conversion both ways."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personal_os_backend.jobs_career_text import (
    CareerError,
    parse_decimal,
    parse_location,
    parse_month,
    parse_range,
    render_decimal,
    render_location,
    render_month,
    render_range,
    render_title,
    split_title,
)

# AGENTS.md: bullets carry no keyword emphasis, and headings never use an em dash.
EMPHASIS = re.compile(r"\*\*|__|\\textbf|\\emph|</?(?:b|strong|em)>", re.IGNORECASE)
SECTIONS: tuple[tuple[str, Literal["experience", "research", "project", "activity"]], ...] = (
    ("experience", "experience"),
    ("research", "research"),
    ("projects", "project"),
    ("activities", "activity"),
)
CONTACT_FACTS = {
    "name": "identity.legal_name",
    "phone": "contact.phone",
    "email": "contact.email",
    "linkedin": "contact.linkedin_url",
    "github": "contact.github_url",
    "citizenship": "work_auth.us_citizen",
}


def _no_em_dash(value: str) -> str:
    if "—" in value:
        raise ValueError("headings separate parts with | or a comma, never an em dash")
    return value


def _bullet_rules(value: list[str]) -> list[str]:
    cleaned: list[str] = []
    for bullet in value:
        text = bullet.strip()
        if not text:
            raise ValueError("a bullet cannot be blank")
        if ";" in text:
            raise ValueError(f"bullets never use semicolons; split or cut: {text[:60]!r}")
        if EMPHASIS.search(text):
            raise ValueError(f"bullets carry no keyword emphasis: {text[:60]!r}")
        if len(text) > 1000:
            raise ValueError("a bullet cannot exceed 1000 characters")
        cleaned.append(text)
    return cleaned


class CareerContact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    phone: str = ""
    email: str = ""
    linkedin: str = ""
    github: str = ""
    citizenship: str = ""


class CareerEducation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    school: str = Field(min_length=1, max_length=300)
    location: str = Field(default="", max_length=200)
    level: Literal["high_school", "undergraduate", "graduate"] = "undergraduate"
    # Exactly one school in the document is primary; the API enforces it.
    primary: bool = False
    onResume: bool = True
    degree: str = Field(default="", max_length=300)
    threads: str = Field(default="", max_length=300)
    courses: list[str] = Field(default_factory=list, max_length=30)
    graduation: str = Field(default="", max_length=100)
    gpa: str = Field(default="", max_length=20)
    gpaScale: str = Field(default="", max_length=20)

    @field_validator("courses")
    @classmethod
    def courses_are_short_items(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for course in value:
            text = course.strip()
            if not text:
                raise ValueError("a course cannot be blank")
            if len(text) > 200:
                raise ValueError("a course cannot exceed 200 characters")
            cleaned.append(text)
        return cleaned


class CareerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Naming an existing entry's id keeps it: it is updated in place, so the
    # library's links survive. An omitted id inserts a new entry.
    id: UUID | None = None
    title: str = Field(min_length=1, max_length=300)
    org: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=200)
    qualifier: str | None = Field(default=None, max_length=300)
    # What work-history forms ask beyond the heading; `location` is rendered from
    # the first three, and the last two stand in for `title` and `org` on a form.
    city: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=200)
    jobTitle: str | None = Field(default=None, max_length=300)
    employer: str | None = Field(default=None, max_length=300)
    date: str = Field(default="", max_length=100)
    onResume: bool = True
    # The printed bullets, then the kept ones the resume omits.
    bullets: list[str] = Field(default_factory=list, max_length=40)
    moreBullets: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("title", "org", "qualifier", "jobTitle", "employer")
    @classmethod
    def heading_has_no_em_dash(cls, value: str | None) -> str | None:
        return None if value is None else _no_em_dash(value)

    @field_validator("bullets", "moreBullets")
    @classmethod
    def bullets_follow_the_resume_rules(cls, value: list[str]) -> list[str]:
        return _bullet_rules(value)

    @model_validator(mode="after")
    def printed_entry_has_a_bullet(self) -> CareerEntry:
        if self.onResume and not self.bullets:
            raise ValueError("an entry on the resume needs at least one bullet")
        return self


class CareerSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=200)
    items: str = Field(min_length=1, max_length=1000)


class CareerDocument(BaseModel):
    """The resume in the shape `os hunt resume put` writes and GET renders."""

    model_config = ConfigDict(extra="forbid")

    contact: CareerContact
    education: list[CareerEducation] = Field(min_length=1, max_length=10)
    experience: list[CareerEntry] = Field(default_factory=list, max_length=20)
    research: list[CareerEntry] = Field(default_factory=list, max_length=20)
    projects: list[CareerEntry] = Field(default_factory=list, max_length=20)
    activities: list[CareerEntry] = Field(default_factory=list, max_length=20)
    skills: list[CareerSkill] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def one_school_is_primary(self) -> CareerDocument:
        primaries = sum(1 for school in self.education if school.primary)
        if primaries != 1:
            raise ValueError("education needs exactly one primary school")
        return self


class EducationRow(BaseModel):
    school: str
    location: str | None
    level: str
    is_primary: bool
    on_resume: bool
    degree_type: str | None
    field_of_study: str | None
    concentrations: str | None
    coursework: list[str]
    graduation_date: date | None
    gpa: Decimal | None
    gpa_scale: Decimal | None


class EntryRow(BaseModel):
    # Always set on a stored row; None for a document entry that has no id yet.
    id: UUID | None = None
    section: Literal["experience", "research", "project", "activity"]
    title: str
    org: str | None
    city: str | None
    region: str | None
    country: str | None
    qualifier: str | None
    job_title: str | None
    employer: str | None
    employment_type: str | None
    start_date: date | None
    end_date: date | None
    on_resume: bool
    # The printed bullets, then the kept ones the resume omits.
    bullets: list[str]
    more_bullets: list[str]


class SkillRow(BaseModel):
    category: str
    items: list[str]


class CareerRecord(BaseModel):
    """The rows, for deriving facts, and the document rendered from them."""

    education: EducationRow
    schools: list[EducationRow]
    entries: list[EntryRow]
    skills: list[SkillRow]
    document: dict[str, Any]


def _text(value: str | None) -> str | None:
    return (value.strip() or None) if value else None


def entry_location(entry: CareerEntry) -> tuple[str | None, str | None, str | None]:
    """An entry's city, region, and country; `location` alone is parsed into them.

    Given both, the location must be what the parts render to, so the document
    never holds two places for one entry.
    """
    parts = (_text(entry.city), _text(entry.region), _text(entry.country))
    location = _text(entry.location)
    if not any(parts):
        return parse_location(location or "")
    if location is not None and location != render_location(*parts):
        raise CareerError(
            f"{entry.title}: location {location!r} is rendered from city, region, and "
            f"country, which give {render_location(*parts)!r}; change those instead"
        )
    return parts


def rows_from_document(
    document: CareerDocument,
) -> tuple[list[EducationRow], list[EntryRow], list[SkillRow]]:
    schools: list[EducationRow] = []
    for education in document.education:
        degree_type, _, field_of_study = education.degree.strip().partition(" ")
        schools.append(
            EducationRow(
                school=education.school.strip(),
                location=education.location.strip() or None,
                level=education.level,
                is_primary=education.primary,
                on_resume=education.onResume,
                degree_type=degree_type or None,
                field_of_study=field_of_study.strip() or None,
                concentrations=education.threads.strip() or None,
                coursework=list(education.courses),
                graduation_date=(
                    parse_month(education.graduation) if education.graduation.strip() else None
                ),
                gpa=parse_decimal(education.gpa, "gpa"),
                gpa_scale=parse_decimal(education.gpaScale, "gpaScale"),
            )
        )
    entries: list[EntryRow] = []
    for key, section in SECTIONS:
        for entry in getattr(document, key):
            title, employment_type = split_title(section, entry.title)
            start, end = parse_range(entry.date)
            city, region, country = entry_location(entry)
            entries.append(
                EntryRow(
                    # An id the document names is checked against the user's
                    # rows at write time; an unset one inserts.
                    id=entry.id,
                    section=section,
                    title=title,
                    org=entry.org.strip() if entry.org else None,
                    city=city,
                    region=region,
                    country=country,
                    qualifier=entry.qualifier.strip() if entry.qualifier else None,
                    job_title=_text(entry.jobTitle),
                    employer=_text(entry.employer),
                    employment_type=employment_type,
                    start_date=start,
                    end_date=end,
                    on_resume=entry.onResume,
                    bullets=entry.bullets,
                    more_bullets=entry.moreBullets,
                )
            )
    skills = [
        SkillRow(
            category=skill.category.strip(),
            items=[item.strip() for item in skill.items.split(",") if item.strip()],
        )
        for skill in document.skills
    ]
    return schools, entries, skills


def document_from_rows(
    schools: list[EducationRow],
    entries: list[EntryRow],
    skills: list[SkillRow],
    contact: dict[str, str],
) -> dict[str, Any]:
    """The resume document; an entry key is present only when it has a value."""
    rendered: dict[str, Any] = {
        "contact": contact,
        "education": [
            {
                "school": education.school,
                "location": education.location or "",
                "level": education.level,
                "primary": education.is_primary,
                "onResume": education.on_resume,
                "degree": " ".join(
                    part
                    for part in (education.degree_type, education.field_of_study)
                    if part
                ),
                "gpa": render_decimal(education.gpa),
                "gpaScale": render_decimal(education.gpa_scale),
                "graduation": render_month(education.graduation_date)
                if education.graduation_date
                else "",
                "threads": education.concentrations or "",
                "courses": list(education.coursework),
            }
            for education in schools
        ],
    }
    for key, section in SECTIONS:
        items: list[dict[str, Any]] = []
        for entry in (item for item in entries if item.section == section):
            item: dict[str, Any] = {"title": render_title(entry.title, entry.employment_type)}
            if entry.id is not None:
                item["id"] = str(entry.id)
            fields = {
                "org": entry.org,
                "location": render_location(entry.city, entry.region, entry.country),
                "city": entry.city,
                "region": entry.region,
                "country": entry.country,
                "qualifier": entry.qualifier,
                "jobTitle": entry.job_title,
                "employer": entry.employer,
            }
            item.update({name: value for name, value in fields.items() if value})
            item["date"] = render_range(entry.start_date, entry.end_date)
            item["onResume"] = entry.on_resume
            item["bullets"] = list(entry.bullets)
            item["moreBullets"] = list(entry.more_bullets)
            items.append(item)
        rendered[key] = items
    rendered["skills"] = [
        {"category": skill.category, "items": ", ".join(skill.items)} for skill in skills
    ]
    return rendered


def contact_conflicts(document: CareerDocument, derived: dict[str, str]) -> list[str]:
    """The facts a document's header disagrees with, as `field: set <fact key>`."""
    given = document.contact.model_dump()
    return [
        f"contact.{name} comes from the fact {CONTACT_FACTS[name]}; "
        f"set it with 'os hunt facts set' instead"
        for name, value in given.items()
        if value.strip() != derived.get(name, "")
    ]


RecordRows = tuple[list[EducationRow], list[EntryRow], list[SkillRow]]
