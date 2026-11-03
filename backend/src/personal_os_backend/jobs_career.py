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
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Protocol
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg import AsyncConnection

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_career_document import (
    CareerDocument,
    CareerRecord,
    contact_conflicts,
    document_from_rows,
    rows_from_document,
)
from personal_os_backend.jobs_career_pdf import (
    PdfError,
    ResumeCompiler,
    get_pdf_compiler,
)
from personal_os_backend.jobs_career_store import (
    CAREER_LOCK,
    CareerStore,
    PostgresCareerStore,
    contact_facts,
    read_record,
)
from personal_os_backend.jobs_career_text import (
    CareerError,
    contact_from_facts,
    parse_location,
    parse_range,
    render_location,
    render_range,
    split_title,
)
from personal_os_backend.jobs_storage import (
    JobsDocumentStorage,
    ResumeStorage,
    get_document_storage,
)

__all__ = [
    "CareerDocument",
    "CareerError",
    "CareerRecord",
    "ResumeConflict",
    "ResumeRebuilder",
    "contact_conflicts",
    "contact_facts",
    "contact_from_facts",
    "document_from_rows",
    "get_career_store",
    "get_resume_rebuilder",
    "parse_location",
    "parse_range",
    "render_location",
    "render_range",
    "router",
    "rows_from_document",
    "split_title",
]


async def get_career_store(request: Request) -> CareerStore:
    settings: Settings = request.app.state.settings
    return PostgresCareerStore(request.app.state.pool, settings.database_pool_timeout_seconds)


class ResumeConflict(Exception):
    """The career record changed while a fact-triggered resume rebuild ran."""


class ResumeRebuilder(Protocol):
    async def prepare(
        self, user_id: UUID, header: dict[str, Any]
    ) -> Callable[[AsyncConnection[Any]], Awaitable[None]] | None: ...


class PostgresResumeRebuilder:
    """Rebuilds the stored resume PDF around a header fact write.

    `prepare` renders and builds before the fact's transaction — a resume that
    will not build refuses the write. The hook it returns runs inside that
    transaction: it locks the record, confirms what it built is still what the
    record renders, and stores the PDF, so a failed upload saves neither.
    """

    def __init__(
        self,
        store: CareerStore,
        compiler: ResumeCompiler,
        storage: ResumeStorage | None,
    ) -> None:
        self._store = store
        self._compiler = compiler
        self._storage = storage

    async def prepare(
        self, user_id: UUID, header: dict[str, Any]
    ) -> Callable[[AsyncConnection[Any]], Awaitable[None]] | None:
        if self._storage is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Document storage is not configured",
            )
        record = await self._store.get_record(user_id)
        if record is None:
            # No record means no resume: the fact is written alone.
            return None
        printed = self._compiler.printed_document(
            document_from_rows(
                record.schools, record.entries, record.skills,
                contact_from_facts(header),
            )
        )
        pdf = await asyncio.to_thread(self._compiler.build_resume_pdf, printed)
        compiler, storage = self._compiler, self._storage

        async def after(connection: AsyncConnection[Any]) -> None:
            await connection.execute(CAREER_LOCK, (user_id,))
            current = await read_record(connection, user_id)
            if current is None or compiler.printed_document(current.document) != printed:
                raise ResumeConflict
            await storage.upload_resume(user_id, pdf)

        return after


async def get_resume_rebuilder(request: Request) -> ResumeRebuilder:
    settings: Settings = request.app.state.settings
    try:
        storage: ResumeStorage | None = JobsDocumentStorage(settings)
    except RuntimeError:
        storage = None
    return PostgresResumeRebuilder(
        PostgresCareerStore(request.app.state.pool, settings.database_pool_timeout_seconds),
        await get_pdf_compiler(request),
        storage,
    )


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
