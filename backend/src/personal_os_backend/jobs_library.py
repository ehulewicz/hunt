"""The narrative library: prose keyed by code, kept beside the career record.

An entry is a piece of writing or a direction for it — an interest, evidence,
a story, a note — named by a dotted code (1.1, 2.2.11, 5.1.E2, 6.1.P3), and
optionally bound to the resume entry it backs. At fill time the worker
composes long-form answers from it. Every write here
records a `jobs_changes` row in the same transaction, and only an explicit
DELETE removes an entry. Each entry can carry a GPTZero human share set by a
scan; any change to the answer clears it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_changes import record_change

CODE = re.compile(r"^[0-9]+(\.[0-9]+)*(\.[A-Z][0-9]+)?$")
Section = Literal["interest", "direction", "evidence", "story", "entry", "note"]

LIBRARY_COLUMNS = """
    id, code, section, entry_id, subject, prompt, guidance, answer,
    display_order, human_percent, created_at, updated_at
"""


class LibraryError(ValueError):
    """A library write that is not allowed, with the reason."""


class LibraryEntryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=CODE.pattern, max_length=40)
    section: Section
    # The resume entry this writing backs, when it backs one.
    entry_id: UUID | None = None
    subject: str | None = Field(default=None, max_length=300)
    prompt: str = Field(min_length=1, max_length=2000)
    guidance: str = Field(default="", max_length=5000)
    answer: str | None = Field(default=None, max_length=20000)
    display_order: int = Field(ge=0)

    @field_validator("prompt")
    @classmethod
    def prompt_asks_something(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("prompt must not be blank")
        return text

    @field_validator("subject", "answer", mode="before")
    @classmethod
    def blank_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class LibraryEntry(BaseModel):
    id: UUID
    code: str
    section: Section
    entry_id: UUID | None
    subject: str | None
    prompt: str
    guidance: str
    answer: str | None
    display_order: int
    # GPTZero's share the answer reads as human; a scan sets it, an answer
    # change clears it. Read-only here: no write model carries it.
    human_percent: int | None = None
    created_at: datetime
    updated_at: datetime

    def fields(self) -> dict[str, Any]:
        """The written fields, for a change record or a no-change comparison."""
        return self.model_dump(
            mode="json", exclude={"id", "created_at", "updated_at", "human_percent"}
        )


class HumanPercentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    percent: int = Field(ge=0, le=100)


class LibraryStore(Protocol):
    async def list_entries(self, user_id: UUID) -> list[LibraryEntry]: ...

    async def set_human_percent(
        self, user_id: UUID, code: str, percent: int
    ) -> LibraryEntry | None: ...

    async def put_entry(
        self, user_id: UUID, code: str, item: LibraryEntryWrite
    ) -> LibraryEntry | None: ...

    async def put_entries(
        self, user_id: UUID, items: list[LibraryEntryWrite]
    ) -> list[LibraryEntry]: ...

    async def delete_entry(self, user_id: UUID, code: str) -> bool: ...

    async def import_entries(
        self, user_id: UUID, items: list[LibraryEntryWrite]
    ) -> list[LibraryEntry] | None: ...


async def _entry(
    connection: AsyncConnection[Any], user_id: UUID, code: str
) -> LibraryEntry | None:
    cursor = await connection.execute(
        f"select {LIBRARY_COLUMNS} from app.jobs_library_entries "
        "where user_id = %s and code = %s",
        (user_id, code),
    )
    row = await cursor.fetchone()
    return LibraryEntry.model_validate(row) if row else None


async def _check_entry_id(
    connection: AsyncConnection[Any], user_id: UUID, item: LibraryEntryWrite
) -> None:
    if item.entry_id is None:
        return
    cursor = await connection.execute(
        "select 1 from app.jobs_resume_entries where id = %s and user_id = %s",
        (item.entry_id, user_id),
    )
    if await cursor.fetchone() is None:
        raise LibraryError(f"entry_id {item.entry_id} is not one of your resume entries")


async def _upsert(
    connection: AsyncConnection[Any], user_id: UUID, item: LibraryEntryWrite
) -> LibraryEntry:
    """Insert or update by code; the caller records the change."""
    cursor = await connection.execute(
        f"""
        insert into app.jobs_library_entries (
          user_id, code, section, entry_id, subject, prompt, guidance, answer,
          display_order
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (user_id, code) do update set
          section = excluded.section,
          entry_id = excluded.entry_id,
          subject = excluded.subject,
          prompt = excluded.prompt,
          guidance = excluded.guidance,
          answer = excluded.answer,
          display_order = excluded.display_order,
          human_percent = case
            when app.jobs_library_entries.answer is distinct from excluded.answer
            then null else app.jobs_library_entries.human_percent end
        returning {LIBRARY_COLUMNS}
        """,
        (
            user_id, item.code, item.section, item.entry_id, item.subject,
            item.prompt, item.guidance, item.answer, item.display_order,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("Library upsert returned no row")
    return LibraryEntry.model_validate(row)


async def _write_entry(
    connection: AsyncConnection[Any], user_id: UUID, item: LibraryEntryWrite
) -> LibraryEntry:
    """Upsert one entry and record its change; a no-change upsert does neither."""
    await _check_entry_id(connection, user_id, item)
    before = await _entry(connection, user_id, item.code)
    written = item.model_dump(mode="json")
    if before is not None and before.fields() == written:
        return before
    after = await _upsert(connection, user_id, item)
    await record_change(
        connection,
        user_id,
        subject="library",
        subject_id=after.code,
        action="create" if before is None else "update",
        before=before.fields() if before is not None else None,
        after=after.fields(),
        origin="you",
    )
    return after


async def _list(connection: AsyncConnection[Any], user_id: UUID) -> list[LibraryEntry]:
    cursor = await connection.execute(
        f"select {LIBRARY_COLUMNS} from app.jobs_library_entries "
        "where user_id = %s order by display_order, code",
        (user_id,),
    )
    return [LibraryEntry.model_validate(row) for row in await cursor.fetchall()]


class PostgresLibraryStore:
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def list_entries(self, user_id: UUID) -> list[LibraryEntry]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            return await _list(connection, user_id)

    async def put_entry(
        self, user_id: UUID, code: str, item: LibraryEntryWrite
    ) -> LibraryEntry | None:
        if item.code != code:
            return None
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            return await _write_entry(connection, user_id, item)

    async def set_human_percent(
        self, user_id: UUID, code: str, percent: int
    ) -> LibraryEntry | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            before = await _entry(connection, user_id, code)
            if before is None:
                return None
            if before.answer is None:
                raise LibraryError("the entry has no answer to score")
            if before.human_percent == percent:
                return before
            cursor = await connection.execute(
                "update app.jobs_library_entries set human_percent = %s "
                "where user_id = %s and code = %s "
                f"returning {LIBRARY_COLUMNS}",
                (percent, user_id, code),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("Human-percent update returned no row")
            after = LibraryEntry.model_validate(row)
            await record_change(
                connection,
                user_id,
                subject="library",
                subject_id=after.code,
                action="update",
                before={"human_percent": before.human_percent},
                after={"human_percent": after.human_percent},
                origin="you",
            )
        return after

    async def put_entries(
        self, user_id: UUID, items: list[LibraryEntryWrite]
    ) -> list[LibraryEntry]:
        """Upsert a list by code in one transaction; what it omits is kept."""
        codes = [item.code for item in items]
        if len(codes) != len(set(codes)):
            raise LibraryError("the body names a code twice")
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            for item in items:
                await _write_entry(connection, user_id, item)
            return await _list(connection, user_id)

    async def delete_entry(self, user_id: UUID, code: str) -> bool:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            before = await _entry(connection, user_id, code)
            if before is None:
                return False
            await connection.execute(
                "delete from app.jobs_library_entries where user_id = %s and code = %s",
                (user_id, code),
            )
            await record_change(
                connection,
                user_id,
                subject="library",
                subject_id=before.code,
                action="delete",
                before=before.fields(),
                after=None,
                origin="you",
            )
        return True

    async def import_entries(
        self, user_id: UUID, items: list[LibraryEntryWrite]
    ) -> list[LibraryEntry] | None:
        """The first load of an empty library; None when it already has entries."""
        codes = [item.code for item in items]
        if len(codes) != len(set(codes)):
            raise LibraryError("the body names a code twice")
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                "select 1 from app.jobs_library_entries where user_id = %s limit 1",
                (user_id,),
            )
            if await cursor.fetchone() is not None:
                return None
            for item in items:
                await _write_entry(connection, user_id, item)
            return await _list(connection, user_id)


async def get_library_store(request: Request) -> LibraryStore:
    settings: Settings = request.app.state.settings
    return PostgresLibraryStore(request.app.state.pool, settings.database_pool_timeout_seconds)


router = APIRouter(prefix="/v1/jobs/library", tags=["library"])
Store = Annotated[LibraryStore, Depends(get_library_store)]


@router.get("", response_model=list[LibraryEntry])
async def list_library(principal: CurrentPrincipal, store: Store) -> list[LibraryEntry]:
    return await store.list_entries(principal.user_id)


@router.put("/{code}", response_model=LibraryEntry)
async def put_library_entry(
    code: str, item: LibraryEntryWrite, principal: CurrentPrincipal, store: Store
) -> LibraryEntry:
    try:
        entry = await store.put_entry(principal.user_id, code, item)
    except LibraryError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    if entry is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "path and body codes differ"
        )
    return entry


@router.put("", response_model=list[LibraryEntry])
async def put_library(
    items: list[LibraryEntryWrite], principal: CurrentPrincipal, store: Store
) -> list[LibraryEntry]:
    try:
        return await store.put_entries(principal.user_id, items)
    except LibraryError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None


@router.put("/{code}/human-percent", response_model=LibraryEntry)
async def put_human_percent(
    code: str, item: HumanPercentWrite, principal: CurrentPrincipal, store: Store
) -> LibraryEntry:
    try:
        entry = await store.set_human_percent(principal.user_id, code, item.percent)
    except LibraryError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No library entry has that code")
    return entry


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_library_entry(
    code: str, principal: CurrentPrincipal, store: Store
) -> Response:
    if not await store.delete_entry(principal.user_id, code):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No library entry has that code")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/import", response_model=list[LibraryEntry], status_code=status.HTTP_201_CREATED)
async def import_library(
    items: list[LibraryEntryWrite], principal: CurrentPrincipal, store: Store
) -> list[LibraryEntry]:
    try:
        entries = await store.import_entries(principal.user_id, items)
    except LibraryError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    if entries is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The library already has entries; edit them at /library or with "
            "os hunt library put",
        )
    return entries
