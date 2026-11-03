"""The career record in Postgres: read as rows with their document, and replaced from a document."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from personal_os_backend.jobs_career_document import (
    CONTACT_FACTS,
    CareerDocument,
    CareerRecord,
    EducationRow,
    EntryRow,
    RecordRows,
    SkillRow,
    contact_conflicts,
    document_from_rows,
)
from personal_os_backend.jobs_career_text import (
    CareerError,
    contact_from_facts,
    render_location,
)
from personal_os_backend.jobs_storage import ResumeStorage


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


# Serialize writers on the user's record before deciding anything; the
# resume rebuild a header fact triggers takes the same lock.
CAREER_LOCK = "select pg_advisory_xact_lock(hashtext('jobs_career:' || %s::text))"


async def read_record(
    connection: AsyncConnection[Any], user_id: UUID
) -> CareerRecord | None:
    """The whole career record, or None when it has no primary school."""
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


class PostgresCareerStore:
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def get_record(self, user_id: UUID) -> CareerRecord | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            return await read_record(connection, user_id)

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
            await connection.execute(CAREER_LOCK, (user_id,))
            conflicts = contact_conflicts(document, await contact_facts(connection, user_id))
            if conflicts:
                raise CareerError("; ".join(conflicts))
            cursor = await connection.execute(
                "select id from app.jobs_resume_entries where user_id = %s", (user_id,)
            )
            existing = {UUID(str(row["id"])) for row in await cursor.fetchall()}
            await _write_schools(connection, user_id, schools)
            kept: set[UUID] = set()
            order: dict[str, int] = {}
            for entry in entries:
                position = order.get(entry.section, 0)
                order[entry.section] = position + 1
                entry_id = await _write_entry(
                    connection, user_id, entry, position, existing, kept
                )
                kept.add(entry_id)
                await _write_bullets(connection, user_id, entry_id, entry)
            await connection.execute(
                "delete from app.jobs_resume_entries where user_id = %s and not (id = any(%s))",
                (user_id, list(kept)),
            )
            await _write_skills(connection, user_id, skills)
            # Inside the transaction: an upload failure rolls the write back.
            await storage.upload_resume(user_id, pdf)
            return await read_record(connection, user_id)


async def _write_schools(
    connection: AsyncConnection[Any], user_id: UUID, schools: list[EducationRow]
) -> None:
    """Replace the user's schools, in document order."""
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


async def _write_entry(
    connection: AsyncConnection[Any],
    user_id: UUID,
    entry: EntryRow,
    position: int,
    existing: set[UUID],
    kept: set[UUID],
) -> UUID:
    """Update the entry the document names by id, or insert a new one; return its id."""
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
    return entry_id


async def _write_bullets(
    connection: AsyncConnection[Any], user_id: UUID, entry_id: UUID, entry: EntryRow
) -> None:
    """Replace an entry's bullets: the printed ones, then the kept ones."""
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


async def _write_skills(
    connection: AsyncConnection[Any], user_id: UUID, skills: list[SkillRow]
) -> None:
    """Replace the user's skill categories, in document order."""
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
