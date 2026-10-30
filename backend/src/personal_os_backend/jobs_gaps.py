"""Prose gaps and application-scoped prose reuse.

A gap records a long-form question the library covers only partly or not at
all, so the Inbox can hold it until the writing exists. A question carries at
most one open gap: posting again updates it, and resolving it frees the next.

`application_prose` is the only prose a run's context carries: this
application's own saved answers, latest per question, approved or not. Saved
prose never feeds a different application.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_answers import (
    PROSE_SELECT,
    Prose,
    ProseGap,
    upsert_question,
)

Family = Literal[
    "why_company",
    "why_role",
    "proudest_work",
    "technical_deep_dive",
    "hardest_problem",
    "ownership",
    "ai_tools",
    "learning_goals",
    "growth_story",
    "collaboration",
    "named_area_experience",
    "strengths",
    "personal_summary",
    "anything_else",
    "other",
]

GAP_SELECT = """
    select gap.id, gap.question_id, question.sample_label as question,
           gap.application_id, company.name as company_name, posting.title,
           gap.family, gap.coverage, gap.missing_detail, gap.created_at,
           gap.resolved_at
    from app.jobs_prose_gaps gap
    left join app.jobs_questions question on question.id = gap.question_id
    left join app.jobs_applications application on application.id = gap.application_id
    left join app.jobs_postings posting on posting.id = application.posting_id
    left join app.jobs_companies company on company.id = posting.company_id
"""


class ProseGapWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    application_id: UUID | None = None
    family: Family
    coverage: Literal["partial", "missing"]
    missing_detail: str = Field(default="", max_length=2000)


async def application_prose(
    connection: AsyncConnection[Any], user_id: UUID, application_id: UUID
) -> list[Prose]:
    """This application's own saved prose, latest row per question."""
    cursor = await connection.execute(
        f"""
        select distinct on (prose.question_id) *
        from (
          {PROSE_SELECT} where prose.user_id = %s and prose.application_id = %s
        ) prose
        order by prose.question_id, prose.updated_at desc
        """,
        (user_id, application_id),
    )
    return [Prose.model_validate(row) for row in await cursor.fetchall()]


async def open_gaps(connection: AsyncConnection[Any], user_id: UUID) -> list[ProseGap]:
    """The unresolved gaps, newest first; the Inbox carries them."""
    cursor = await connection.execute(
        f"{GAP_SELECT} where gap.user_id = %s and gap.resolved_at is null "
        "order by gap.created_at desc",
        (user_id,),
    )
    return [ProseGap.model_validate(row) for row in await cursor.fetchall()]


class GapStore(Protocol):
    async def create_gap(self, user_id: UUID, item: ProseGapWrite) -> ProseGap | None: ...

    async def list_gaps(self, user_id: UUID, *, open_only: bool) -> list[ProseGap]: ...

    async def resolve_gap(self, user_id: UUID, gap_id: UUID) -> ProseGap | None: ...


class PostgresGapStore:
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def create_gap(self, user_id: UUID, item: ProseGapWrite) -> ProseGap | None:
        """Record the question, then the one open gap for it; None when the
        label asks nothing."""
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            question_id = await upsert_question(
                connection, user_id=user_id, label=item.question, form="long"
            )
            if question_id is None:
                return None
            cursor = await connection.execute(
                """
                insert into app.jobs_prose_gaps (
                  user_id, question_id, application_id, family, coverage,
                  missing_detail
                )
                select %s, %s,
                       (select id from app.jobs_applications
                        where id = %s and user_id = %s),
                       %s, %s, %s
                on conflict (user_id, question_id) where resolved_at is null
                do update set
                  family = excluded.family,
                  coverage = excluded.coverage,
                  missing_detail = excluded.missing_detail,
                  application_id = coalesce(
                    excluded.application_id, app.jobs_prose_gaps.application_id
                  )
                returning id
                """,
                (
                    user_id, question_id, item.application_id, user_id,
                    item.family, item.coverage, item.missing_detail,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("Gap upsert returned no id")
            cursor = await connection.execute(
                f"{GAP_SELECT} where gap.id = %s", (row["id"],)
            )
            gap = await cursor.fetchone()
        return ProseGap.model_validate(gap) if gap else None

    async def list_gaps(self, user_id: UUID, *, open_only: bool) -> list[ProseGap]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"{GAP_SELECT} where gap.user_id = %s "
                "and (%s::boolean is false or gap.resolved_at is null) "
                "order by gap.created_at desc",
                (user_id, open_only),
            )
            return [ProseGap.model_validate(row) for row in await cursor.fetchall()]

    async def resolve_gap(self, user_id: UUID, gap_id: UUID) -> ProseGap | None:
        """Close an open gap; None when no open gap has that id."""
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                update app.jobs_prose_gaps set resolved_at = now()
                where id = %s and user_id = %s and resolved_at is null
                returning id
                """,
                (gap_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            cursor = await connection.execute(
                f"{GAP_SELECT} where gap.id = %s", (row["id"],)
            )
            gap = await cursor.fetchone()
        return ProseGap.model_validate(gap) if gap else None


async def get_gap_store(request: Request) -> GapStore:
    settings: Settings = request.app.state.settings
    return PostgresGapStore(request.app.state.pool, settings.database_pool_timeout_seconds)


router = APIRouter(prefix="/v1/jobs/prose", tags=["gaps"])
Store = Annotated[GapStore, Depends(get_gap_store)]


@router.post("/gaps", response_model=ProseGap, status_code=status.HTTP_201_CREATED)
async def create_gap(
    item: ProseGapWrite, principal: CurrentPrincipal, store: Store
) -> ProseGap:
    gap = await store.create_gap(principal.user_id, item)
    if gap is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "that label asks nothing")
    return gap


@router.get("/gaps", response_model=list[ProseGap])
async def list_gaps(
    principal: CurrentPrincipal,
    store: Store,
    open: Annotated[bool, Query()] = False,
) -> list[ProseGap]:
    return await store.list_gaps(principal.user_id, open_only=open)


@router.post("/gaps/{gap_id}/resolve", response_model=ProseGap)
async def resolve_gap(
    gap_id: UUID, principal: CurrentPrincipal, store: Store
) -> ProseGap:
    gap = await store.resolve_gap(principal.user_id, gap_id)
    if gap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No open gap has that id")
    return gap
