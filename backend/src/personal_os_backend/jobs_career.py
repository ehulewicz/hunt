"""The career record: the resume as rows, and the document assembled from them.

Education, experience, research, projects, bullets, and skills are rows with
real dates. The resume document — the shape `artifacts/career/main.json` has
always had — is rendered from them, and a document written back is parsed into
them in one transaction, so the database is the one writable copy and the JSON
and PDF are exports. The header's contact details are facts, not rows: the
document's `contact` block is derived from `jobs_facts` and a write must agree
with it.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg import AsyncConnection, sql
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings

# The authored range separator in the resume ("May 2025 \u2013 Present").
EN_DASH = "\u2013"
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
RANGE = re.compile(r"^(?P<start>.+?)\s+[\u2013-]\s+(?P<end>.+)$")
# AGENTS.md: bullets carry no keyword emphasis, and headings never use an em dash.
EMPHASIS = re.compile(r"\*\*|__|\\textbf|\\emph|</?(?:b|strong|em)>", re.IGNORECASE)
SECTIONS: tuple[tuple[str, Literal["experience", "research", "project"]], ...] = (
    ("experience", "experience"),
    ("research", "research"),
    ("projects", "project"),
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
    if "\u2014" in value:
        raise ValueError("headings separate parts with | or a comma, never an em dash")
    return value


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
    degree: str = Field(default="", max_length=300)
    threads: str = Field(default="", max_length=300)
    courses: str = Field(default="", max_length=1000)
    graduation: str = Field(default="", max_length=100)
    gpa: str = Field(default="", max_length=20)
    gpaScale: str = Field(default="", max_length=20)


class CareerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    bullets: list[str] = Field(min_length=1, max_length=20)

    @field_validator("title", "org", "qualifier", "jobTitle", "employer")
    @classmethod
    def heading_has_no_em_dash(cls, value: str | None) -> str | None:
        return None if value is None else _no_em_dash(value)

    @field_validator("bullets")
    @classmethod
    def bullets_follow_the_resume_rules(cls, value: list[str]) -> list[str]:
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


class CareerSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=200)
    items: str = Field(min_length=1, max_length=1000)


class CareerDocument(BaseModel):
    """The resume in the shape the PDF template reads and `main.json` stores."""

    model_config = ConfigDict(extra="forbid")

    # `meta` was the old file's bookkeeping; it is accepted on import and dropped.
    meta: dict[str, Any] | None = Field(default=None, exclude=True)
    contact: CareerContact
    education: CareerEducation
    experience: list[CareerEntry] = Field(default_factory=list, max_length=20)
    research: list[CareerEntry] = Field(default_factory=list, max_length=20)
    projects: list[CareerEntry] = Field(default_factory=list, max_length=20)
    skills: list[CareerSkill] = Field(default_factory=list, max_length=20)


class EducationRow(BaseModel):
    school: str
    location: str | None
    degree_type: str | None
    field_of_study: str | None
    concentrations: str | None
    coursework: str | None
    graduation_date: date | None
    gpa: Decimal | None
    gpa_scale: Decimal | None


class EntryRow(BaseModel):
    section: Literal["experience", "research", "project"]
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
    bullets: list[str]


class SkillRow(BaseModel):
    category: str
    items: list[str]


class CareerRecord(BaseModel):
    """The rows, for deriving facts, and the document rendered from them."""

    education: EducationRow
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
) -> tuple[EducationRow, list[EntryRow], list[SkillRow]]:
    education = document.education
    degree_type, _, field_of_study = education.degree.strip().partition(" ")
    education_row = EducationRow(
        school=education.school.strip(),
        location=education.location.strip() or None,
        degree_type=degree_type or None,
        field_of_study=field_of_study.strip() or None,
        concentrations=education.threads.strip() or None,
        coursework=education.courses.strip() or None,
        graduation_date=(
            parse_month(education.graduation) if education.graduation.strip() else None
        ),
        gpa=parse_decimal(education.gpa, "gpa"),
        gpa_scale=parse_decimal(education.gpaScale, "gpaScale"),
    )
    entries: list[EntryRow] = []
    for key, section in SECTIONS:
        for entry in getattr(document, key):
            title, employment_type = split_title(section, entry.title)
            start, end = parse_range(entry.date)
            city, region, country = entry_location(entry)
            entries.append(
                EntryRow(
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
                    bullets=entry.bullets,
                )
            )
    skills = [
        SkillRow(
            category=skill.category.strip(),
            items=[item.strip() for item in skill.items.split(",") if item.strip()],
        )
        for skill in document.skills
    ]
    return education_row, entries, skills


def document_from_rows(
    education: EducationRow,
    entries: list[EntryRow],
    skills: list[SkillRow],
    contact: dict[str, str],
) -> dict[str, Any]:
    """The resume document; a key is present only when it has a value, as authored."""
    rendered: dict[str, Any] = {
        "contact": contact,
        "education": {
            "school": education.school,
            "location": education.location or "",
            "degree": " ".join(
                part for part in (education.degree_type, education.field_of_study) if part
            ),
            "threads": education.concentrations or "",
            "courses": education.coursework or "",
            "graduation": render_month(education.graduation_date)
            if education.graduation_date
            else "",
            "gpa": render_decimal(education.gpa),
            "gpaScale": render_decimal(education.gpa_scale),
        },
    }
    for key, section in SECTIONS:
        items: list[dict[str, Any]] = []
        for entry in (item for item in entries if item.section == section):
            item: dict[str, Any] = {"title": render_title(entry.title, entry.employment_type)}
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
            item["bullets"] = list(entry.bullets)
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


class CareerStore(Protocol):
    async def get_record(self, user_id: UUID) -> CareerRecord | None: ...

    async def write_record(
        self, user_id: UUID, document: CareerDocument, *, only_if_empty: bool
    ) -> CareerRecord | None: ...


async def _contact(connection: AsyncConnection[Any], user_id: UUID) -> dict[str, str]:
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
            select school, location, degree_type, field_of_study, concentrations,
                   coursework, graduation_date, gpa, gpa_scale
            from app.jobs_education where user_id = %s and is_primary
            """,
            (user_id,),
        )
        education = await cursor.fetchone()
        if education is None:
            return None
        cursor = await connection.execute(
            """
            select entry.section, entry.title, entry.org, entry.city, entry.region,
                   entry.country, entry.qualifier, entry.job_title, entry.employer,
                   entry.employment_type, entry.start_date, entry.end_date,
                   coalesce(
                     array_agg(bullet.text order by bullet.display_order)
                       filter (where bullet.id is not null),
                     array[]::text[]
                   ) as bullets
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
        education_row = EducationRow.model_validate(education)
        return CareerRecord(
            education=education_row,
            entries=entries,
            skills=skills,
            document=document_from_rows(
                education_row, entries, skills, await _contact(connection, user_id)
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
        self, user_id: UUID, document: CareerDocument, *, only_if_empty: bool
    ) -> CareerRecord | None:
        """Replace the whole record from a document; None when `only_if_empty` and one exists."""
        education, entries, skills = rows_from_document(document)
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            # Serialize writers on the user's record before deciding anything.
            await connection.execute(
                "select pg_advisory_xact_lock(hashtext('jobs_career:' || %s::text))", (user_id,)
            )
            cursor = await connection.execute(
                "select 1 from app.jobs_education where user_id = %s limit 1", (user_id,)
            )
            if only_if_empty and await cursor.fetchone() is not None:
                return None
            conflicts = contact_conflicts(document, await _contact(connection, user_id))
            if conflicts:
                raise CareerError("; ".join(conflicts))
            for table in ("jobs_resume_entries", "jobs_skills", "jobs_education"):
                await connection.execute(
                    sql.SQL("delete from app.{} where user_id = %s").format(sql.Identifier(table)),
                    (user_id,),
                )
            await connection.execute(
                """
                insert into app.jobs_education (
                  user_id, school, location, degree_type, field_of_study, concentrations,
                  coursework, graduation_date, gpa, gpa_scale, is_primary, display_order
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, 0)
                """,
                (
                    user_id, education.school, education.location, education.degree_type,
                    education.field_of_study, education.concentrations, education.coursework,
                    education.graduation_date, education.gpa, education.gpa_scale,
                ),
            )
            order: dict[str, int] = {}
            for entry in entries:
                position = order.get(entry.section, 0)
                order[entry.section] = position + 1
                cursor = await connection.execute(
                    """
                    insert into app.jobs_resume_entries (
                      user_id, section, title, org, location, city, region, country,
                      qualifier, job_title, employer, employment_type,
                      start_date, end_date, display_order
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        # `location` is written, rendered, only for an API still
                        # reading it; nothing here reads it back.
                        user_id, entry.section, entry.title, entry.org,
                        render_location(entry.city, entry.region, entry.country) or None,
                        entry.city, entry.region, entry.country, entry.qualifier,
                        entry.job_title, entry.employer, entry.employment_type,
                        entry.start_date, entry.end_date, position,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise RuntimeError("Resume entry insert returned no id")
                for index, bullet in enumerate(entry.bullets):
                    await connection.execute(
                        """
                        insert into app.jobs_resume_bullets (
                          user_id, entry_id, text, display_order
                        ) values (%s, %s, %s, %s)
                        """,
                        (user_id, row["id"], bullet, index),
                    )
            for index, skill in enumerate(skills):
                await connection.execute(
                    """
                    insert into app.jobs_skills (user_id, category, items, display_order)
                    values (%s, %s, %s, %s)
                    """,
                    (user_id, skill.category, skill.items, index),
                )
            return await self._read(connection, user_id)


async def get_career_store(request: Request) -> CareerStore:
    settings: Settings = request.app.state.settings
    return PostgresCareerStore(request.app.state.pool, settings.database_pool_timeout_seconds)


router = APIRouter(prefix="/v1/jobs/career", tags=["career"])
Store = Annotated[CareerStore, Depends(get_career_store)]


@router.get("", response_model=CareerRecord)
async def get_career(principal: CurrentPrincipal, store: Store) -> CareerRecord:
    record = await store.get_record(principal.user_id)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No career record yet; run 'os hunt resume import'"
        )
    return record


async def _write(
    store: CareerStore, user_id: UUID, document: CareerDocument, *, only_if_empty: bool
) -> CareerRecord | None:
    try:
        return await store.write_record(user_id, document, only_if_empty=only_if_empty)
    except CareerError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None


@router.put("", response_model=CareerRecord)
async def put_career(
    document: CareerDocument, principal: CurrentPrincipal, store: Store
) -> CareerRecord:
    record = await _write(store, principal.user_id, document, only_if_empty=False)
    if record is None:
        raise RuntimeError("A replacing write returned no record")
    return record


@router.post("/import", response_model=CareerRecord, status_code=status.HTTP_201_CREATED)
async def import_career(
    document: CareerDocument, principal: CurrentPrincipal, store: Store
) -> CareerRecord:
    record = await _write(store, principal.user_id, document, only_if_empty=True)
    if record is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The career record already exists; edit it with 'os hunt resume edit'",
        )
    return record
