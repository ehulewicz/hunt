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
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status

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
    CareerStore,
    PostgresCareerStore,
    contact_facts,
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
from personal_os_backend.jobs_storage import ResumeStorage, get_document_storage

__all__ = [
    "CareerDocument",
    "CareerError",
    "CareerRecord",
    "contact_conflicts",
    "contact_facts",
    "contact_from_facts",
    "document_from_rows",
    "get_career_store",
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
