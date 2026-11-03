"""Prose and the archive: stored long-form writing, and the old answer library.

Runs never write here: `jobs_prose` is read-only reference rows, listed and
deleted, and the narrative library is the only approved writing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from psycopg import AsyncConnection
from pydantic import BaseModel

from personal_os_backend.config import StoreBase
from personal_os_backend.jobs_changes import Action, Origin, record_change


class Prose(BaseModel):
    id: UUID
    question_id: UUID | None
    question_label: str | None
    application_id: UUID | None
    company_name: str | None
    text: str
    approved: bool
    origin: Literal["manual", "model", "archive"]
    # Whether the application it was written for went in.
    submitted: bool = False
    created_at: datetime
    updated_at: datetime


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
           prose.origin, prose.submitted, prose.created_at, prose.updated_at
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

    async def delete_prose(self, user_id: UUID, prose_id: UUID) -> bool:
        """Prune an old reference row; the answer itself stays in the application history."""
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
