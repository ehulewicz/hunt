"""The one unauthenticated read: the stored resume PDF, for sharing a link.

Only `GET /v1/public/resume.pdf` exists here, and it serves exactly one object —
the private bucket's current resume for the single configured user — as an
inline PDF that must be revalidated. Nothing else under `/v1/public` may appear
without a product decision.
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from personal_os_backend.config import Settings
from personal_os_backend.jobs_storage import ResumeStorage, get_document_storage

router = APIRouter(prefix="/v1/public", tags=["public"])
Storage = Annotated[ResumeStorage, Depends(get_document_storage)]


def _strong_etag(pdf: bytes) -> str:
    return f'"{hashlib.sha256(pdf).hexdigest()}"'


def _if_none_match(header: str | None, etag: str) -> bool:
    """True when If-None-Match names this representation, including a weak tag."""
    if not header:
        return False
    opaque = etag[1:-1]
    for part in header.split(","):
        token = part.strip()
        if token == "*":
            return True
        if token.startswith("W/"):
            token = token[2:].strip()
        if len(token) >= 2 and token[0] == '"' and token[-1] == '"' and token[1:-1] == opaque:
            return True
    return False


@router.get("/resume.pdf")
async def public_resume(request: Request, storage: Storage) -> Response:
    settings: Settings = request.app.state.settings
    pdf = await storage.download_resume(settings.allowed_user_id)
    if pdf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No resume is stored yet")
    etag = _strong_etag(pdf)
    headers = {
        "Content-Disposition": 'inline; filename="Alex_Example.pdf"',
        "Cache-Control": "no-cache",
        "ETag": etag,
    }
    if _if_none_match(request.headers.get("if-none-match"), etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=pdf, media_type="application/pdf", headers=headers)
