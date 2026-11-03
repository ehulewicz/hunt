"""Prose and the archive: long-form writing and where it came from, and the old answer library."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_os_backend.database import StoreBase
from personal_os_backend.jobs_changes import Action, Origin, record_change
from personal_os_backend.jobs_questions import upsert_question


class ProseSpan(BaseModel):
    """A stretch of prose and where it came from; text in no span is yours."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    source: Literal["model", "previous", "library"]
    # The library entry's code, present exactly when the source is `library`.
    entry: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def entry_marks_a_library_span(self) -> ProseSpan:
        if (self.source == "library") != (self.entry is not None and bool(self.entry.strip())):
            raise ValueError("a span names its library entry, and only a library span has one")
        return self


def check_spans(spans: list[ProseSpan], text: str) -> None:
    """Spans run in order, never overlap, and stay inside the text; ValueError if not."""
    reached = 0
    for span in spans:
        if span.start < reached or span.end <= span.start or span.end > len(text):
            raise ValueError("provenance spans must be ordered, apart, and inside the text")
        reached = span.end


def stripped_spans(text: str, spans: list[ProseSpan] | None) -> list[dict[str, Any]] | None:
    """The spans of `text` as they fall in `text.strip()`, which is what is saved."""
    if spans is None:
        return None
    lead, kept = len(text) - len(text.lstrip()), len(text.strip())
    shifted = []
    for span in spans:
        start, end = max(0, span.start - lead), min(kept, span.end - lead)
        if end > start:
            shifted.append(
                {
                    "start": start,
                    "end": end,
                    "source": span.source,
                    **({"entry": span.entry} if span.source == "library" else {}),
                }
            )
    return shifted


class Prose(BaseModel):
    id: UUID
    question_id: UUID | None
    question_label: str | None
    application_id: UUID | None
    company_name: str | None
    text: str
    approved: bool
    origin: Literal["manual", "model", "archive"]
    # Where each stretch came from; None for prose saved before spans were kept.
    provenance: list[ProseSpan] | None = None
    # Whether the application it was written for went in.
    submitted: bool = False
    created_at: datetime
    updated_at: datetime


class ProseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    text: str = Field(min_length=1, max_length=20000)
    approved: bool = True
    # Writing you add is yours; writing copied out of the archive says so.
    origin: Literal["manual", "archive"] = "manual"


class ProseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=20000)
    approved: bool | None = None
    origin: Literal["you", "inbox"] = "you"

    @model_validator(mode="after")
    def something_changes(self) -> ProseUpdate:
        if self.text is None and self.approved is None:
            raise ValueError("give text, approved, or both")
        return self


class ArchivedAnswer(BaseModel):
    id: UUID
    question: str | None
    normalized_key: str
    answer_text: str
    reuse_policy: str
    approved: bool
    updated_at: datetime


PROSE_SELECT = """
    select prose.id, prose.question_id, question.sample_label as question_label,
           prose.application_id, company.name as company_name, prose.text, prose.approved,
           prose.origin, prose.provenance, prose.submitted, prose.created_at, prose.updated_at
    from app.jobs_prose prose
    left join app.jobs_questions question on question.id = prose.question_id
    left join app.jobs_applications application on application.id = prose.application_id
    left join app.jobs_postings posting on posting.id = application.posting_id
    left join app.jobs_companies company on company.id = posting.company_id
"""


async def archived_answers(
    connection: AsyncConnection[Any], user_id: UUID
) -> list[ArchivedAnswer]:
    cursor = await connection.execute(
        """
        select id, normalized_question as question, normalized_key, answer_text,
               reuse_policy, approved, updated_at
        from app.jobs_archive_answers where user_id = %s
        order by updated_at desc, id
        """,
        (user_id,),
    )
    return [ArchivedAnswer.model_validate(row) for row in await cursor.fetchall()]


class ProseStore(Protocol):
    async def list_prose(self, user_id: UUID) -> list[Prose]: ...

    async def create_prose(self, user_id: UUID, item: ProseCreate) -> Prose: ...

    async def update_prose(
        self, user_id: UUID, prose_id: UUID, item: ProseUpdate
    ) -> Prose | None: ...

    async def delete_prose(self, user_id: UUID, prose_id: UUID) -> bool: ...

    async def list_archive(self, user_id: UUID) -> list[ArchivedAnswer]: ...


async def _record_prose(
    connection: AsyncConnection[Any],
    user_id: UUID,
    action: Action,
    before: Prose | None,
    after: Prose | None,
    *,
    origin: Origin,
) -> None:
    """Record a prose change: its text and whether it is approved, before and after."""
    subject = after or before
    if subject is None:
        return

    def shown(prose: Prose | None) -> dict[str, Any] | None:
        if prose is None:
            return None
        return {"question": prose.question_label, "text": prose.text, "approved": prose.approved}

    if shown(before) == shown(after):
        return
    await record_change(
        connection, user_id, subject="prose", subject_id=str(subject.id), action=action,
        before=shown(before), after=shown(after), origin=origin,
    )


class ProseMixin(StoreBase):
    async def list_prose(self, user_id: UUID) -> list[Prose]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"{PROSE_SELECT} where prose.user_id = %s "
                "order by prose.approved desc, prose.updated_at desc",
                (user_id,),
            )
            return [Prose.model_validate(row) for row in await cursor.fetchall()]

    async def create_prose(self, user_id: UUID, item: ProseCreate) -> Prose:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            question_id = await upsert_question(
                connection, user_id=user_id, label=item.question, form="long"
            )
            cursor = await connection.execute(
                """
                insert into app.jobs_prose (user_id, question_id, text, approved, origin)
                values (%s, %s, %s, %s, %s) returning id
                """,
                (user_id, question_id, item.text.strip(), item.approved, item.origin),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("Prose insert returned no id")
            cursor = await connection.execute(
                f"{PROSE_SELECT} where prose.id = %s", (row["id"],)
            )
            created = await cursor.fetchone()
            prose = Prose.model_validate(created)
            await _record_prose(connection, user_id, "create", None, prose, origin="you")
        return prose

    async def update_prose(
        self, user_id: UUID, prose_id: UUID, item: ProseUpdate
    ) -> Prose | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"{PROSE_SELECT} where prose.id = %s and prose.user_id = %s",
                (prose_id, user_id),
            )
            found = await cursor.fetchone()
            if found is None:
                return None
            cursor = await connection.execute(
                """
                update app.jobs_prose
                set text = coalesce(%s, text), approved = coalesce(%s, approved)
                where id = %s and user_id = %s returning id
                """,
                (item.text.strip() if item.text else None, item.approved, prose_id, user_id),
            )
            if await cursor.fetchone() is None:
                return None
            cursor = await connection.execute(f"{PROSE_SELECT} where prose.id = %s", (prose_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            before, after = Prose.model_validate(found), Prose.model_validate(row)
            await _record_prose(connection, user_id, "update", before, after, origin=item.origin)
        return after

    async def delete_prose(self, user_id: UUID, prose_id: UUID) -> bool:
        """Prose that should not be reused; the answer itself stays in the application history."""
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"{PROSE_SELECT} where prose.id = %s and prose.user_id = %s",
                (prose_id, user_id),
            )
            found = await cursor.fetchone()
            cursor = await connection.execute(
                "delete from app.jobs_prose where id = %s and user_id = %s", (prose_id, user_id)
            )
            deleted = bool(cursor.rowcount > 0)
            if deleted and found is not None:
                await _record_prose(
                    connection, user_id, "delete", Prose.model_validate(found), None,
                    origin="you",
                )
        return deleted

    async def list_archive(self, user_id: UUID) -> list[ArchivedAnswer]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            return await archived_answers(connection, user_id)
