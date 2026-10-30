from __future__ import annotations

from typing import Protocol
from urllib.parse import quote
from uuid import UUID

import httpx
from fastapi import HTTPException, Request, status

from personal_os_backend.config import Settings

AUTOFILL_BUCKET = "jobs-autofill-artifacts"
DOCUMENTS_BUCKET = "jobs-documents"

# Storage answers a missing object with 404, or with a 400 whose JSON body
# carries the 404: {"statusCode": "404", "error": "not_found"} (older: "Object
# not found").
MISSING_OBJECT_ERRORS = frozenset({"not_found", "Object not found"})


def _missing_object(response: httpx.Response) -> bool:
    if response.status_code != 400:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    return body.get("statusCode") == "404" or body.get("error") in MISSING_OBJECT_ERRORS


class ResumeStorage(Protocol):
    """The private `jobs-documents` copy of the compiled resume PDF."""

    async def upload_resume(self, user_id: UUID, content: bytes) -> None: ...

    async def download_resume(self, user_id: UUID) -> bytes | None: ...


class JobsDocumentStorage:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if settings.supabase_secret_key is None:
            raise RuntimeError("Supabase document storage is not configured")
        self._root = str(settings.supabase_url).rstrip("/")
        self._secret = settings.supabase_secret_key.get_secret_value()
        self._transport = transport

    async def upload_autofill_artifact(self, path: str, content: bytes, content_type: str) -> None:
        if content_type not in {"image/png", "text/html", "text/plain", "application/json"}:
            raise ValueError("Unsupported autofill artifact type")
        encoded_path = quote(path, safe="/")
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
            response = await client.post(
                f"{self._root}/storage/v1/object/{AUTOFILL_BUCKET}/{encoded_path}",
                headers={
                    "apikey": self._secret,
                    "Authorization": f"Bearer {self._secret}",
                    "Content-Type": content_type,
                    "x-upsert": "false",
                },
                content=content,
            )
            response.raise_for_status()

    async def download_autofill_artifact(self, path: str) -> bytes:
        encoded_path = quote(path, safe="/")
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
            response = await client.get(
                f"{self._root}/storage/v1/object/authenticated/{AUTOFILL_BUCKET}/{encoded_path}",
                headers={
                    "apikey": self._secret,
                    "Authorization": f"Bearer {self._secret}",
                },
            )
            response.raise_for_status()
            return response.content

    async def upload_resume(self, user_id: UUID, content: bytes) -> None:
        """The current resume PDF for a user, replaced on every career save."""
        path = quote(f"resume/{user_id}/current.pdf", safe="/")
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
            response = await client.post(
                f"{self._root}/storage/v1/object/{DOCUMENTS_BUCKET}/{path}",
                headers={
                    "apikey": self._secret,
                    "Authorization": f"Bearer {self._secret}",
                    "Content-Type": "application/pdf",
                    "x-upsert": "true",
                },
                content=content,
            )
            response.raise_for_status()

    async def download_resume(self, user_id: UUID) -> bytes | None:
        """The stored resume PDF, or None when the record has none yet."""
        path = quote(f"resume/{user_id}/current.pdf", safe="/")
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
            response = await client.get(
                f"{self._root}/storage/v1/object/authenticated/{DOCUMENTS_BUCKET}/{path}",
                headers={
                    "apikey": self._secret,
                    "Authorization": f"Bearer {self._secret}",
                },
            )
            if response.status_code == 404 or _missing_object(response):
                return None
            response.raise_for_status()
            return response.content


async def get_document_storage(request: Request) -> JobsDocumentStorage:
    try:
        return JobsDocumentStorage(request.app.state.settings)
    except RuntimeError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Document storage is not configured"
        ) from None
