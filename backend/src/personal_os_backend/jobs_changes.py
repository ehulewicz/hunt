"""Changes and sightings: the history of what Jobs knows, and what it is checked against.

A change records a pattern or fact before and after, who made it, and the
failure it answered. The pattern and fact writes record theirs in the same
transaction (`record_change`), so the history misses none. A change that was
learned arrives with its verification: how many recorded sightings the worker
replayed offline, and how many the change made wrong. A code change the
maintenance path lands is posted here with its commit.

A sighting is a short question answered in an application that went in: its
label, the control, the options it offered when they were kept, what the
worker filled then (as a hash), and the answer that was submitted, which is
the answer you accepted. An answer recorded before `jobs_questions` existed
has no question row; its own normalized key stands in, and meets its run's fill
events by it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings

Subject = Literal["pattern", "fact", "code"]
Action = Literal["create", "update", "retire", "reject", "delete"]
Origin = Literal["you", "inbox", "model", "repair"]
ChangeStatus = Literal["pending", "passed", "failed"]
COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
# A question key as `normalize_answer_key` writes one.
QUESTION_KEY = r"^[a-z0-9+#]+(?: [a-z0-9+#]+)*$"
CHANGE_COLUMNS = """
    id, subject, subject_id, action, before, after, origin, run_id, question_key,
    status, sampled, failed, created_at, verified_at
"""
# The most sightings one request returns; the worker samples from them.
SIGHTINGS_LIMIT = 2000


class Verification(BaseModel):
    """What a replay of the recorded sightings found."""

    model_config = ConfigDict(extra="forbid")

    sampled: int = Field(ge=0)
    failed: int = Field(ge=0)

    @model_validator(mode="after")
    def failed_were_sampled(self) -> Self:
        if self.failed > self.sampled:
            raise ValueError("a replay cannot fail more sightings than it sampled")
        return self


class Failure(BaseModel):
    """The failure a change answered: the run, and the question that failed in it."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID | None = None
    question_key: str | None = Field(
        default=None, min_length=1, max_length=1000, pattern=QUESTION_KEY
    )


class Change(BaseModel):
    id: UUID
    subject: Subject
    subject_id: str
    action: Action
    before: Any
    after: Any
    origin: Origin
    run_id: UUID | None
    question_key: str | None
    status: ChangeStatus | None
    sampled: int | None
    failed: int | None
    created_at: datetime
    verified_at: datetime | None


class CodeChangeCreate(BaseModel):
    """A code change the maintenance path landed, with its commit."""

    model_config = ConfigDict(extra="forbid")

    commit: str = Field(pattern=COMMIT.pattern)
    origin: Literal["repair", "you"] = "repair"
    failure: Failure | None = None
    # None records it pending: its replay has not reported.
    verification: Verification | None = None


class ReplayKnowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: dict[str, Any] = Field(max_length=5000)
    types: dict[str, str] = Field(max_length=5000)
    seed: str = Field(default="", max_length=200)


class Sighting(BaseModel):
    question: str
    question_key: str
    # The registry's control kind and attributes, from the fill event, when there was one.
    kind: str | None
    attributes: dict[str, Any] | None
    # The control as the answer recorded it: its input type or tag (`radio`, `select`).
    control: str | None
    options: list[str] | None
    answer: str
    fact_key: str | None
    formatter: str | None
    value_hash: str | None
    seen_at: datetime
    knowledge: ReplayKnowledge | None = None


async def record_change(
    connection: AsyncConnection[Any],
    user_id: UUID,
    *,
    subject: Subject,
    subject_id: str,
    action: Action,
    before: Any,
    after: Any,
    origin: Origin,
    failure: Failure | None = None,
    verification: Verification | None = None,
    pending: bool = False,
) -> Change:
    """Write one change in the caller's transaction.

    A verification sets the status; `pending` marks a change whose replay has
    not reported; otherwise nothing verifies it. A run that is not this user's
    is not recorded as the failure.
    """
    status_value: ChangeStatus | None = (
        ("failed" if verification.failed else "passed") if verification is not None
        else "pending" if pending else None
    )
    cursor = await connection.execute(
        f"""
        insert into app.jobs_changes (
          user_id, subject, subject_id, action, before, after, origin, run_id, question_key,
          status, sampled, failed, verified_at
        ) values (
          %s, %s, %s, %s, %s, %s, %s,
          (select id from app.jobs_autofill_runs where id = %s and user_id = %s),
          %s, %s, %s, %s, case when %s then now() end
        )
        returning {CHANGE_COLUMNS}
        """,
        (
            user_id, subject, subject_id, action,
            Jsonb(before) if before is not None else None,
            Jsonb(after) if after is not None else None,
            origin,
            failure.run_id if failure is not None else None, user_id,
            failure.question_key if failure is not None else None,
            status_value,
            verification.sampled if verification is not None else None,
            verification.failed if verification is not None else None,
            verification is not None,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("The change insert returned no row")
    return Change.model_validate(row)


async def latest_failed(
    connection: AsyncConnection[Any], user_id: UUID, subject_ids: list[str]
) -> dict[str, Change]:
    """Each pattern's latest change, where that change failed its replay."""
    if not subject_ids:
        return {}
    cursor = await connection.execute(
        f"""
        select distinct on (subject_id) {CHANGE_COLUMNS}
        from app.jobs_changes
        where user_id = %s and subject = 'pattern' and subject_id = any(%s)
        order by subject_id, created_at desc
        """,
        (user_id, subject_ids),
    )
    changes = [Change.model_validate(row) for row in await cursor.fetchall()]
    return {change.subject_id: change for change in changes if change.status == "failed"}


class ChangeStore(Protocol):
    async def list_changes(
        self,
        user_id: UUID,
        *,
        subject: Subject | None,
        subject_id: str | None,
        status: ChangeStatus | None,
        limit: int,
    ) -> list[Change]: ...

    async def create_code_change(self, user_id: UUID, item: CodeChangeCreate) -> Change: ...

    async def sightings(self, user_id: UUID, *, limit: int) -> list[Sighting]: ...


class PostgresChangeStore:
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def list_changes(
        self,
        user_id: UUID,
        *,
        subject: Subject | None,
        subject_id: str | None,
        status: ChangeStatus | None,
        limit: int,
    ) -> list[Change]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"""
                select {CHANGE_COLUMNS} from app.jobs_changes
                where user_id = %s
                  and (%s::text is null or subject = %s)
                  and (%s::text is null or subject_id = %s)
                  and (%s::text is null or status = %s)
                order by created_at desc
                limit %s
                """,
                (user_id, subject, subject, subject_id, subject_id, status, status, limit),
            )
            rows = await cursor.fetchall()
        return [Change.model_validate(row) for row in rows]

    async def create_code_change(self, user_id: UUID, item: CodeChangeCreate) -> Change:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            return await record_change(
                connection,
                user_id,
                subject="code",
                subject_id=item.commit,
                action="create",
                before=None,
                after=None,
                origin=item.origin,
                failure=item.failure,
                verification=item.verification,
                pending=item.verification is None,
            )

    async def sightings(self, user_id: UUID, *, limit: int) -> list[Sighting]:
        """Short answers submitted with an application, newest first.

        Each carries the last fill event its run made for that question: the
        options it was asked with, the fact and formatter it was filled from,
        and the hash of what was filled. A question the worker never touched
        has none of them.
        """
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select answer.question_text as question, keyed.question_key, event.kind,
                       event.attributes, answer.control_kind as control, event.options,
                       answer.answer_text as answer, event.fact_key, event.formatter,
                       event.value_hash, event.knowledge, answer.created_at as seen_at
                from app.jobs_application_questions answer
                left join app.jobs_questions question on question.id = answer.question_id
                cross join lateral (
                  select coalesce(question.normalized_key, answer.normalized_key) as question_key
                ) keyed
                left join lateral (
                  select control.kind, control.attributes, fill.options, fill.fact_key,
                         fill.formatter, fill.value_hash, fill.knowledge
                  from app.jobs_fill_events fill
                  join app.jobs_controls control on control.id = fill.control_id
                  where fill.run_id = answer.run_id and fill.question_key = keyed.question_key
                  order by fill.created_at desc
                  limit 1
                ) event on true
                where answer.user_id = %s and answer.event_kind = 'final'
                  and answer.status = 'submitted'
                  and coalesce(keyed.question_key, '') <> ''
                  and coalesce(
                    question.form,
                    case when answer.control_kind = 'textarea' then 'long' else 'short' end
                  ) = 'short'
                  and coalesce(trim(answer.answer_text), '') <> ''
                order by answer.created_at desc
                limit %s
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()
        return [Sighting.model_validate(row) for row in rows]


async def get_change_store(request: Request) -> ChangeStore:
    settings: Settings = request.app.state.settings
    return PostgresChangeStore(request.app.state.pool, settings.database_pool_timeout_seconds)


router = APIRouter(prefix="/v1/jobs", tags=["changes"])
Store = Annotated[ChangeStore, Depends(get_change_store)]


@router.get("/changes", response_model=list[Change])
async def list_changes(
    principal: CurrentPrincipal,
    store: Store,
    subject: Annotated[Subject | None, Query()] = None,
    subject_id: Annotated[str | None, Query(max_length=200)] = None,
    change_status: Annotated[ChangeStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[Change]:
    return await store.list_changes(
        principal.user_id, subject=subject, subject_id=subject_id, status=change_status,
        limit=limit,
    )


@router.post("/changes", response_model=Change, status_code=status.HTTP_201_CREATED)
async def create_code_change(
    item: CodeChangeCreate, principal: CurrentPrincipal, store: Store
) -> Change:
    return await store.create_code_change(principal.user_id, item)


@router.get("/sightings", response_model=list[Sighting])
async def list_sightings(
    principal: CurrentPrincipal,
    store: Store,
    limit: Annotated[int, Query(ge=1, le=SIGHTINGS_LIMIT)] = SIGHTINGS_LIMIT,
) -> list[Sighting]:
    return await store.sightings(principal.user_id, limit=limit)
