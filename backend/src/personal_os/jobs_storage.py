from __future__ import annotations

from urllib.parse import quote

import httpx

from personal_os.config import Settings

BUCKET = "jobs-documents"


class JobsDocumentStorage:
    def __init__(self, settings: Settings) -> None:
        if settings.supabase_secret_key is None:
            raise RuntimeError("Supabase document storage is not configured")
        self._root = str(settings.supabase_url).rstrip("/")
        self._secret = settings.supabase_secret_key.get_secret_value()

    async def upload_pdf(self, path: str, content: bytes) -> None:
        encoded_path = quote(path, safe="/")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._root}/storage/v1/object/{BUCKET}/{encoded_path}",
                headers={
                    "apikey": self._secret,
                    "Authorization": f"Bearer {self._secret}",
                    "Content-Type": "application/pdf",
                    "x-upsert": "false",
                },
                content=content,
            )
            response.raise_for_status()

    async def download_pdf(self, path: str) -> bytes:
        encoded_path = quote(path, safe="/")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self._root}/storage/v1/object/authenticated/{BUCKET}/{encoded_path}",
                headers={
                    "apikey": self._secret,
                    "Authorization": f"Bearer {self._secret}",
                },
            )
            response.raise_for_status()
            return response.content
