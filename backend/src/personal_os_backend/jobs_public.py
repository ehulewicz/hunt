"""The one unauthenticated read: the stored resume PDF, for sharing a link.

Only `GET /v1/public/resume.pdf` exists here, and it serves exactly one object —
the private bucket's current resume for the single configured user — as an
inline PDF with a short cache. Nothing else under `/v1/public` may appear
without a product decision.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from personal_os_backend.config import Settings
from personal_os_backend.jobs_storage import ResumeStorage, get_document_storage

router = APIRouter(prefix="/v1/public", tags=["public"])
Storage = Annotated[ResumeStorage, Depends(get_document_storage)]


@router.get("/resume.pdf")
async def public_resume(request: Request, storage: Storage) -> Response:
    settings: Settings = request.app.state.settings
    pdf = await storage.download_resume(settings.allowed_user_id)
    if pdf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No resume is stored yet")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="Alex_Example.pdf"',
            "Cache-Control": "public, max-age=60",
        },
    )
