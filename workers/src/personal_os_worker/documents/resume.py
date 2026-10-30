"""The career record's resume, fetched through the API.

The record in the database is the only resume: `os hunt resume get|put` reads
and writes it, and the backend builds the one-page PDF on each save. The
worker downloads that PDF for uploads and never builds one itself.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from personal_os_worker.core.api import WorkerApi


def repository_root() -> Path:
    configured = os.environ.get("PERSONAL_OS_REPOSITORY")
    if configured:
        return Path(configured).expanduser().resolve()
    # workers/src/personal_os_worker/documents/resume.py -> repository root
    return Path(__file__).resolve().parents[4]


def transcript_pdf_path() -> Path | None:
    """The tracked transcript beside the resume, when there is one."""
    path = repository_root() / "artifacts" / "career" / "Transcript.pdf"
    return path if path.exists() else None


RESUME_PDF_NAME = "Alex_Example.pdf"


def download_resume_pdf(api: WorkerApi, directory: Path) -> Path:
    """The published resume PDF, written atomically as `directory/Alex_Example.pdf`."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / RESUME_PDF_NAME
    temporary = directory / f".{RESUME_PDF_NAME}.tmp"
    temporary.write_bytes(api.resume_pdf())
    temporary.replace(target)
    return target


def rejection(error: httpx.HTTPStatusError) -> str:
    """What the API said was wrong with a document, as one line."""
    try:
        detail = error.response.json().get("detail")
    except ValueError:
        return error.response.text.strip() or str(error)
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', [])[1:])}: {item.get('msg')}"
            for item in detail
            if isinstance(item, dict)
        )
    return str(detail)
