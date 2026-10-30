"""The career record: the resume as rows, and the document assembled from them.

Education, experience, research, projects, activities, bullets, and skills are
rows with real dates. The resume document — the shape `os hunt resume put`
writes — is rendered from them, and a document written back is parsed into
them in one transaction, so the database is the one writable copy of the
record. Every entry, bullet, and school carries `on_resume`: the
record keeps the material the resume omits. Entry rows are stable across
writes — an entry the document names by id is updated, not replaced — so the
library's `entry_id` links survive a save. The header's contact details are
facts, not rows: the document's `contact` block is derived from `jobs_facts`
and a write must agree with it. A save compiles the printed projection of the
rendered document (`jobs_career_pdf.py`) and stores the PDF beside the record;
a failed compile refuses the write before anything is stored.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_career_pdf import (
    PdfError,
    ResumeCompiler,
    get_pdf_compiler,
)
from personal_os_backend.jobs_storage import ResumeStorage, get_document_storage

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


class CareerStore(Protocol):
    async def get_record(self, user_id: UUID) -> CareerRecord | None: ...

    async def write_record(
        self,
        user_id: UUID,
        document: CareerDocument,
        *,
        rows: RecordRows,
        pdf: bytes,
        storage: ResumeStorage,
    ) -> CareerRecord | None: ...


async def contact_facts(connection: AsyncConnection[Any], user_id: UUID) -> dict[str, str]:
    """The resume header for this user, derived from the contact facts."""
    cursor = await connection.execute(
        "select key, value from app.jobs_facts where user_id = %s and key = any(%s)",
        (user_id, list(CONTACT_FACTS.values())),
    )
    return contact_from_facts({str(row["key"]): row["value"] for row in await cursor.fetchall()})


class PostgresCareerStore:
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def _read(self, connection: AsyncConnection[Any], user_id: UUID) -> CareerRecord | None:
        cursor = await connection.execute(
            """
            select school, location, level, is_primary, on_resume, degree_type,
                   field_of_study, concentrations, coursework, graduation_date,
                   gpa, gpa_scale
            from app.jobs_education where user_id = %s order by display_order
            """,
            (user_id,),
        )
        schools = [EducationRow.model_validate(row) for row in await cursor.fetchall()]
        primary = next((school for school in schools if school.is_primary), None)
        if primary is None:
            return None
        cursor = await connection.execute(
            """
            select entry.id, entry.section, entry.title, entry.org, entry.city,
                   entry.region, entry.country, entry.qualifier, entry.job_title,
                   entry.employer, entry.employment_type, entry.start_date,
                   entry.end_date, entry.on_resume,
                   coalesce(
                     array_agg(bullet.text order by bullet.display_order)
                       filter (where bullet.on_resume),
                     array[]::text[]
                   ) as bullets,
                   coalesce(
                     array_agg(bullet.text order by bullet.display_order)
                       filter (where not bullet.on_resume),
                     array[]::text[]
                   ) as more_bullets
            from app.jobs_resume_entries entry
            left join app.jobs_resume_bullets bullet on bullet.entry_id = entry.id
            where entry.user_id = %s
            group by entry.id
            order by entry.section, entry.display_order
            """,
            (user_id,),
        )
        entries = [EntryRow.model_validate(row) for row in await cursor.fetchall()]
        cursor = await connection.execute(
            "select category, items from app.jobs_skills where user_id = %s order by display_order",
            (user_id,),
        )
        skills = [SkillRow.model_validate(row) for row in await cursor.fetchall()]
        return CareerRecord(
            education=primary,
            schools=schools,
            entries=entries,
            skills=skills,
            document=document_from_rows(
                schools, entries, skills, await contact_facts(connection, user_id)
            ),
        )

    async def get_record(self, user_id: UUID) -> CareerRecord | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            return await self._read(connection, user_id)

    async def write_record(
        self,
        user_id: UUID,
        document: CareerDocument,
        *,
        rows: RecordRows,
        pdf: bytes,
        storage: ResumeStorage,
    ) -> CareerRecord | None:
        """Replace the record from a document and store the resume's PDF.

        Entry ids are stable: a document entry that names one of the user's
        rows updates it, one without an id is inserted, and the user's rows
        absent from the document are deleted. The PDF upload happens inside
        the transaction, so an upload failure saves nothing.
        """
        schools, entries, skills = rows
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            # Serialize writers on the user's record before deciding anything.
            await connection.execute(
                "select pg_advisory_xact_lock(hashtext('jobs_career:' || %s::text))", (user_id,)
            )
            conflicts = contact_conflicts(document, await contact_facts(connection, user_id))
            if conflicts:
                raise CareerError("; ".join(conflicts))
            cursor = await connection.execute(
                "select id from app.jobs_resume_entries where user_id = %s", (user_id,)
            )
            existing = {UUID(str(row["id"])) for row in await cursor.fetchall()}
            await connection.execute(
                "delete from app.jobs_education where user_id = %s", (user_id,)
            )
            for index, education in enumerate(schools):
                await connection.execute(
                    """
                    insert into app.jobs_education (
                      user_id, school, location, level, is_primary, on_resume,
                      degree_type, field_of_study, concentrations, coursework,
                      graduation_date, gpa, gpa_scale, display_order
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id, education.school, education.location, education.level,
                        education.is_primary, education.on_resume, education.degree_type,
                        education.field_of_study, education.concentrations,
                        education.coursework, education.graduation_date, education.gpa,
                        education.gpa_scale, index,
                    ),
                )
            kept: set[UUID] = set()
            order: dict[str, int] = {}
            for entry in entries:
                position = order.get(entry.section, 0)
                order[entry.section] = position + 1
                # `location` is written, rendered, only for an API still
                # reading it; nothing here reads it back.
                location = render_location(entry.city, entry.region, entry.country) or None
                if entry.id is not None:
                    if entry.id in kept:
                        raise CareerError(f"duplicate entry id {entry.id}")
                    if entry.id not in existing:
                        raise CareerError(f"unknown entry id {entry.id}")
                    await connection.execute(
                        """
                        update app.jobs_resume_entries set
                          section = %s, title = %s, org = %s, location = %s, city = %s,
                          region = %s, country = %s, qualifier = %s, job_title = %s,
                          employer = %s, employment_type = %s, start_date = %s,
                          end_date = %s, on_resume = %s, display_order = %s
                        where id = %s and user_id = %s
                        """,
                        (
                            entry.section, entry.title, entry.org, location, entry.city,
                            entry.region, entry.country, entry.qualifier, entry.job_title,
                            entry.employer, entry.employment_type, entry.start_date,
                            entry.end_date, entry.on_resume, position, entry.id, user_id,
                        ),
                    )
                    entry_id = entry.id
                else:
                    cursor = await connection.execute(
                        """
                        insert into app.jobs_resume_entries (
                          user_id, section, title, org, location, city, region, country,
                          qualifier, job_title, employer, employment_type,
                          start_date, end_date, on_resume, display_order
                        ) values (
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        returning id
                        """,
                        (
                            user_id, entry.section, entry.title, entry.org, location,
                            entry.city, entry.region, entry.country, entry.qualifier,
                            entry.job_title, entry.employer, entry.employment_type,
                            entry.start_date, entry.end_date, entry.on_resume, position,
                        ),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise RuntimeError("Resume entry insert returned no id")
                    entry_id = UUID(str(row["id"]))
                kept.add(entry_id)
                await connection.execute(
                    "delete from app.jobs_resume_bullets where entry_id = %s", (entry_id,)
                )
                for index, (text, printed) in enumerate(
                    [(bullet, True) for bullet in entry.bullets]
                    + [(bullet, False) for bullet in entry.more_bullets]
                ):
                    await connection.execute(
                        """
                        insert into app.jobs_resume_bullets (
                          user_id, entry_id, text, on_resume, display_order
                        ) values (%s, %s, %s, %s, %s)
                        """,
                        (user_id, entry_id, text, printed, index),
                    )
            await connection.execute(
                "delete from app.jobs_resume_entries where user_id = %s and not (id = any(%s))",
                (user_id, list(kept)),
            )
            await connection.execute(
                "delete from app.jobs_skills where user_id = %s", (user_id,)
            )
            for index, skill in enumerate(skills):
                await connection.execute(
                    """
                    insert into app.jobs_skills (user_id, category, items, display_order)
                    values (%s, %s, %s, %s)
                    """,
                    (user_id, skill.category, skill.items, index),
                )
            # Inside the transaction: an upload failure rolls the write back.
            await storage.upload_resume(user_id, pdf)
            return await self._read(connection, user_id)


async def get_career_store(request: Request) -> CareerStore:
    settings: Settings = request.app.state.settings
    return PostgresCareerStore(request.app.state.pool, settings.database_pool_timeout_seconds)


router = APIRouter(prefix="/v1/jobs/career", tags=["career"])
Store = Annotated[CareerStore, Depends(get_career_store)]
Compiler = Annotated[ResumeCompiler, Depends(get_pdf_compiler)]
Storage = Annotated[ResumeStorage, Depends(get_document_storage)]


@router.get("", response_model=CareerRecord)
async def get_career(principal: CurrentPrincipal, store: Store) -> CareerRecord:
    record = await store.get_record(principal.user_id)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No career record yet; save one at /resume/edit or with os hunt resume put",
        )
    return record


@router.put("", response_model=CareerRecord)
async def put_career(
    document: CareerDocument,
    principal: CurrentPrincipal,
    store: Store,
    compiler: Compiler,
    storage: Storage,
) -> CareerRecord:
    # The compile happens before the transaction, on the rendered document —
    # what GET shows, not the request body — so a resume that will not build
    # writes nothing.
    try:
        rows = rows_from_document(document)
        canonical = document_from_rows(*rows, document.contact.model_dump())
    except CareerError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    try:
        pdf = await asyncio.to_thread(
            compiler.build_resume_pdf, compiler.printed_document(canonical)
        )
    except PdfError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    try:
        record = await store.write_record(
            principal.user_id, document, rows=rows, pdf=pdf, storage=storage
        )
    except CareerError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    except (httpx.HTTPError, RuntimeError):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The resume PDF could not be stored; nothing was saved",
        ) from None
    if record is None:
        raise RuntimeError("A replacing write returned no record")
    return record
