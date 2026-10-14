from __future__ import annotations

from urllib.parse import quote

import httpx

from personal_os_backend.config import Settings

AUTOFILL_BUCKET = "jobs-autofill-artifacts"


class JobsDocumentStorage:
    def __init__(self, settings: Settings) -> None:
        if settings.supabase_secret_key is None:
            raise RuntimeError("Supabase document storage is not configured")
        self._root = str(settings.supabase_url).rstrip("/")
        self._secret = settings.supabase_secret_key.get_secret_value()

    async def upload_autofill_artifact(self, path: str, content: bytes, content_type: str) -> None:
        if content_type not in {"image/png", "text/html", "text/plain", "application/json"}:
            raise ValueError("Unsupported autofill artifact type")
        encoded_path = quote(path, safe="/")
        async with httpx.AsyncClient(timeout=30.0) as client:
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
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self._root}/storage/v1/object/authenticated/{AUTOFILL_BUCKET}/{encoded_path}",
                headers={
                    "apikey": self._secret,
                    "Authorization": f"Bearer {self._secret}",
                },
            )
            response.raise_for_status()
            return response.content
