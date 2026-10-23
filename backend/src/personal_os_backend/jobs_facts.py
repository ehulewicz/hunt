"""Facts: the single store of what is true about the user.

One row per fact, keyed by a stable dotted name. Values are JSON typed by
`value_type`; FastAPI validates a value against its type on every write, and a
database check enforces the same shape. No fact is hidden: Jobs is single-user
and full visibility is what makes a fill readable. Derived facts (completed co-op
rotations, graduation term) are computed by the worker and never stored.
Every set, verification, and deletion is recorded in `jobs_changes` in the same
transaction, keyed by the fact's key. A set that arrives without the worker's
replay (the web Library, the CLI) is recorded pending, and the next run replays
it; a verification or a deletion changes no answer and is never pending.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_changes import (
    Action,
    Failure,
    Origin,
    Verification,
    record_change,
)

FactType = Literal["text", "boolean", "number", "date", "choice", "list", "object"]
FACT_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
FACT_COLUMNS = "key, label, value, value_type, verified_at, updated_at"


def validate_fact_value(value_type: str, value: Any) -> Any:
    """The value, if it has the shape its type declares; ValueError otherwise."""
    if value_type == "text":
        # An empty text is a fact too: the answer is none, and the field stays blank.
        if not isinstance(value, str):
            raise ValueError("A text fact needs a string")
        return value.strip()
    if value_type == "choice":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("A choice fact needs a non-empty string")
        return value.strip()
    if value_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("A boolean fact needs true or false")
        return value
    if value_type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("A number fact needs a number")
        return value
    if value_type == "date":
        if not isinstance(value, str):
            raise ValueError("A date fact needs a YYYY-MM-DD string")
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError("A date fact needs a YYYY-MM-DD string") from None
    if value_type == "list":
        if not isinstance(value, list):
            raise ValueError("A list fact needs a JSON array")
        return value
    if value_type == "object":
        if not isinstance(value, dict):
            raise ValueError("An object fact needs a JSON object")
        return value
    raise ValueError(f"Unknown fact type {value_type!r}")


class Fact(BaseModel):
    key: str
    label: str
    value: Any
    value_type: FactType
    verified_at: datetime | None
    updated_at: datetime


class FactWrite(BaseModel):
    """Set a fact's value. Label and type default to the existing row."""

    model_config = ConfigDict(extra="forbid")

    value: Any
    label: str | None = Field(default=None, min_length=1, max_length=200)
    value_type: FactType | None = None
    verified: bool = True
    # Who set it, and the failure it answers: a fact given mid-run fills a gap.
    origin: Literal["you", "inbox"] = "you"
    failure: Failure | None = None
    # The worker's replay of the new value against the answers you accepted:
    # how many it sampled and how many now disagree. Your fact is saved either
    # way; what disagrees is recorded with it and waits in the Inbox.
    verification: Verification | None = None


class FactStore(Protocol):
    async def list_facts(self, user_id: UUID) -> list[Fact]: ...

    async def set_fact(self, user_id: UUID, key: str, item: FactWrite) -> Fact: ...

    async def verify_fact(self, user_id: UUID, key: str) -> Fact | None: ...

    async def delete_fact(self, user_id: UUID, key: str) -> bool: ...


async def _record_fact(
    connection: AsyncConnection[Any],
    user_id: UUID,
    key: str,
    action: Action,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    origin: Origin = "you",
    failure: Failure | None = None,
    verification: Verification | None = None,
    pending: bool = False,
) -> None:
    def shown(row: dict[str, Any] | None) -> Any:
        return Fact.model_validate(row).model_dump(mode="json") if row else None

    await record_change(
        connection, user_id, subject="fact", subject_id=key, action=action,
        before=shown(before), after=shown(after), origin=origin, failure=failure,
        verification=verification, pending=pending,
    )


class PostgresFactStore:
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def list_facts(self, user_id: UUID) -> list[Fact]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"select {FACT_COLUMNS} from app.jobs_facts where user_id = %s order by key",
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [Fact.model_validate(row) for row in rows]

    async def set_fact(self, user_id: UUID, key: str, item: FactWrite) -> Fact:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"select {FACT_COLUMNS} from app.jobs_facts "
                "where user_id = %s and key = %s for update",
                (user_id, key),
            )
            existing = await cursor.fetchone()
            label = item.label or (existing or {}).get("label")
            value_type = item.value_type or (existing or {}).get("value_type")
            if not label or not value_type:
                raise ValueError("A new fact needs a label and a value_type")
            value = validate_fact_value(value_type, item.value)
            if existing is not None and (
                existing["label"] == label and existing["value_type"] == value_type
                and existing["value"] == value
                and (existing["verified_at"] is not None) == item.verified
            ):
                return Fact.model_validate(existing)
            cursor = await connection.execute(
                f"""
                insert into app.jobs_facts (
                  user_id, key, label, value, value_type, verified_at
                ) values (%s, %s, %s, %s, %s, case when %s then now() end)
                on conflict (user_id, key) do update set
                  label = excluded.label, value = excluded.value,
                  value_type = excluded.value_type,
                  verified_at = excluded.verified_at
                returning {FACT_COLUMNS}
                """,
                (user_id, key, label, Jsonb(value), value_type, item.verified),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("The fact write returned no row")
            # A value no replay has checked yet waits pending: the next run or
            # `os hunt replay` replays it and records what it found.
            await _record_fact(
                connection, user_id, key, "update" if existing else "create", existing, row,
                origin=item.origin, failure=item.failure, verification=item.verification,
                pending=item.verification is None,
            )
        return Fact.model_validate(row)

    async def verify_fact(self, user_id: UUID, key: str) -> Fact | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"select {FACT_COLUMNS} from app.jobs_facts "
                "where user_id = %s and key = %s for update",
                (user_id, key),
            )
            before = await cursor.fetchone()
            cursor = await connection.execute(
                f"update app.jobs_facts set verified_at = now() "
                f"where user_id = %s and key = %s returning {FACT_COLUMNS}",
                (user_id, key),
            )
            row = await cursor.fetchone()
            if row is not None:
                await _record_fact(connection, user_id, key, "update", before, row)
        return Fact.model_validate(row) if row else None

    async def delete_fact(self, user_id: UUID, key: str) -> bool:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"delete from app.jobs_facts where user_id = %s and key = %s "
                f"returning {FACT_COLUMNS}",
                (user_id, key),
            )
            before = await cursor.fetchone()
            if before is not None:
                await _record_fact(connection, user_id, key, "delete", before, None)
        return before is not None


async def get_fact_store(request: Request) -> FactStore:
    settings: Settings = request.app.state.settings
    return PostgresFactStore(request.app.state.pool, settings.database_pool_timeout_seconds)


router = APIRouter(prefix="/v1/jobs/facts", tags=["facts"])
Store = Annotated[FactStore, Depends(get_fact_store)]


def _checked_key(key: str) -> str:
    if len(key) > 200:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "A fact key is at most 200 characters"
        )
    if not FACT_KEY.fullmatch(key):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A fact key is dotted lowercase words, e.g. education.graduation_date",
        )
    return key


@router.get("", response_model=list[Fact])
async def list_facts(principal: CurrentPrincipal, store: Store) -> list[Fact]:
    return await store.list_facts(principal.user_id)


@router.put("/{key}", response_model=Fact)
async def set_fact(key: str, item: FactWrite, principal: CurrentPrincipal, store: Store) -> Fact:
    try:
        return await store.set_fact(principal.user_id, _checked_key(key), item)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None


@router.post("/{key}/verify", response_model=Fact)
async def verify_fact(key: str, principal: CurrentPrincipal, store: Store) -> Fact:
    fact = await store.verify_fact(principal.user_id, _checked_key(key))
    if fact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fact not found")
    return fact


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fact(key: str, principal: CurrentPrincipal, store: Store) -> Response:
    if not await store.delete_fact(principal.user_id, _checked_key(key)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fact not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
