"""The resume and cover letter PDFs, compiled where the record lives.

`PUT /v1/jobs/career` compiles the printed projection of the document it just
accepted and stores the bytes next to the record; `POST
/v1/jobs/cover-letter/pdf` compiles a letter for one application and returns it
without storing anything. Both use the same deterministic build the worker's
`os hunt resume` used: the Jake's Resume template, a content-derived trailer
id, a pinned `SOURCE_DATE_EPOCH`, latexmk, a one-page budget, and a
`pdftotext -layout` check that no bullet wraps past two rendered lines. The
projection is what is compiled — never the record — so an off-resume entry,
bullet, or school, or an entry's id, cannot change the bytes.

TeX must be installed where this runs (the image builds it); a build that
fails raises `PdfError` with the reason and the endpoint refuses the write.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_career_latex import (
    ENTRY_SECTIONS,
    build_latex,
    build_letter_latex,
)

REPRODUCIBLE_EPOCH = "946684800"  # 2000-01-01T00:00:00Z


# A resume build is valid only when the reference-style layout stays on one page.
MAXIMUM_PAGES: int | None = 1


# Keys that say whether a record item prints; the projection drops them.
SCHOOL_META = ("level", "primary", "onResume")
ENTRY_META = ("id", "onResume", "moreBullets")


class PdfError(RuntimeError):
    """A document the build refused: the compile, the page count, or a bullet."""


def printed_document(document: dict[str, Any]) -> dict[str, Any]:
    """The on-resume projection of a career document: exactly what prints.

    Off-resume schools and entries drop out; a printed entry keeps only its
    `bullets`. Ids, `onResume`, `moreBullets`, `primary`, and `level` never
    reach the LaTeX, so editing them cannot change the PDF bytes.
    """
    printed: dict[str, Any] = {
        "contact": dict(document.get("contact") or {}),
        "education": [
            {key: value for key, value in school.items() if key not in SCHOOL_META}
            for school in document.get("education") or []
            if school.get("onResume", True)
        ],
    }
    for key, _ in ENTRY_SECTIONS:
        printed[key] = [
            {name: value for name, value in entry.items() if name not in ENTRY_META}
            for entry in document.get(key) or []
            if entry.get("onResume", True)
        ]
    printed["skills"] = list(document.get("skills") or [])
    return printed


def latex_errors(log: Path) -> list[str]:
    """The `! ...` lines latexmk wrote, which name what actually failed."""
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.startswith("! ")]


def latex_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = REPRODUCIBLE_EPOCH
    environment["FORCE_SOURCE_DATE"] = "1"
    return environment


# AGENTS.md: a resume bullet never runs past two rendered lines.
MAXIMUM_BULLET_LINES = 2
BULLET_MARK = "•"


def build_resume_pdf(printed: dict[str, Any]) -> bytes:
    """Compile the printed projection to the resume PDF's bytes."""
    with tempfile.TemporaryDirectory() as directory:
        pdf = compile_pdf(build_latex(printed), Path(directory), "resume", pages=MAXIMUM_PAGES)
        layout = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if layout.returncode != 0:
            raise PdfError("Could not read the generated resume to check bullet lengths")
        long = overlong_bullets(layout.stdout)
        if long:
            raise PdfError(
                f"A bullet runs past {MAXIMUM_BULLET_LINES} lines; split or cut: "
                + "; ".join(f"{text!r} ({lines} lines)" for text, lines in long)
            )
        return pdf.read_bytes()


def overlong_bullets(layout: str) -> list[tuple[str, int]]:
    """Bullets longer than the limit, read from `pdftotext -layout` output.

    A bullet's first line starts with the bullet mark; its continuation lines
    are indented past the mark. A blank line, a less-indented line, or another
    mark ends it.
    """
    found: list[tuple[str, int]] = []
    current: tuple[str, int, int] | None = None  # (opening words, mark column, lines)

    def close() -> None:
        if current is not None and current[2] > MAXIMUM_BULLET_LINES:
            found.append((current[0], current[2]))

    for line in layout.splitlines():
        stripped = line.strip()
        column = len(line) - len(line.lstrip())
        if stripped.startswith(BULLET_MARK):
            close()
            words = " ".join(stripped.removeprefix(BULLET_MARK).split()[:6])
            current = (words, column, 1)
        elif current is not None and stripped and column > current[1]:
            current = (current[0], current[1], current[2] + 1)
        else:
            close()
            current = None
    close()
    return found


def compile_pdf(latex: str, output_directory: Path, stem: str, *, pages: int | None) -> Path:
    """Run latexmk on `latex` as `<stem>.tex` and verify the page budget."""
    output_directory.mkdir(parents=True, exist_ok=True)
    source = output_directory / f"{stem}.tex"
    source.write_text(latex, encoding="utf-8")
    process = subprocess.run(
        [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-outdir={output_directory}",
            str(source),
        ],
        check=False,
        capture_output=True,
        env=latex_environment(),
        text=True,
        timeout=120,
    )
    pdf = output_directory / f"{stem}.pdf"
    if process.returncode != 0 or not pdf.exists():
        reported = latex_errors(output_directory / f"{stem}.log")
        if not reported:
            console = [
                line.strip()
                for line in (process.stdout + "\n" + process.stderr).splitlines()
                if line.strip()
            ]
            reported = console[-1:] or ["latexmk reported no error"]
        raise PdfError(f"latexmk failed for {stem}: {' '.join(reported[:3])[:500]}")
    page_check = subprocess.run(
        ["pdfinfo", str(pdf)], check=False, capture_output=True, text=True, timeout=15
    )
    found = re.search(r"^Pages:\s+(\d+)$", page_check.stdout, re.MULTILINE)
    if page_check.returncode != 0 or found is None:
        raise PdfError(f"Could not verify the generated {stem} page count")
    if pages is not None and int(found.group(1)) > pages:
        raise PdfError(
            f"{stem} must stay within {pages} page(s); generated {found.group(1)}"
        )
    return pdf


def build_letter_pdf(
    contact: dict[str, Any], text: str, *, date: str, company: str
) -> bytes:
    """Compile a cover letter to one page of PDF bytes."""
    with tempfile.TemporaryDirectory() as directory:
        latex = build_letter_latex(contact, text, date=date, company=company)
        pdf = compile_pdf(latex, Path(directory), "cover_letter", pages=1)
        return pdf.read_bytes()


class ResumeCompiler(Protocol):
    """What the routes ask of the build: the projection, and the two PDFs."""

    def printed_document(self, document: dict[str, Any]) -> dict[str, Any]: ...

    def build_resume_pdf(self, printed: dict[str, Any]) -> bytes: ...

    def build_letter_pdf(
        self, contact: dict[str, Any], text: str, *, date: str, company: str
    ) -> bytes: ...


class LatexCompiler:
    """The module's functions behind the compiler protocol, for injection."""

    def printed_document(self, document: dict[str, Any]) -> dict[str, Any]:
        return printed_document(document)

    def build_resume_pdf(self, printed: dict[str, Any]) -> bytes:
        return build_resume_pdf(printed)

    def build_letter_pdf(
        self, contact: dict[str, Any], text: str, *, date: str, company: str
    ) -> bytes:
        return build_letter_pdf(contact, text, date=date, company=company)


async def get_pdf_compiler(request: Request) -> ResumeCompiler:
    return LatexCompiler()


router = APIRouter(prefix="/v1/jobs", tags=["documents"])
Compiler = Annotated[ResumeCompiler, Depends(get_pdf_compiler)]


class CoverLetterPdf(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20000)
    company: str = Field(max_length=300)
    date: str = Field(max_length=100)


async def _contact(request: Request, user_id: UUID) -> dict[str, str]:
    """The resume header, from the user's contact facts."""
    # Deferred: jobs_career imports this module for the compiler.
    from personal_os_backend.jobs_career import contact_facts

    settings: Settings = request.app.state.settings
    pool: AsyncConnectionPool[Any] = request.app.state.pool
    async with (
        pool.connection(timeout=settings.database_pool_timeout_seconds) as connection,
        connection.transaction(),
    ):
        await connection.execute("set local role personal_os_api")
        return await contact_facts(connection, user_id)


@router.post("/cover-letter/pdf")
async def cover_letter_pdf(
    item: CoverLetterPdf,
    principal: CurrentPrincipal,
    compiler: Compiler,
    request: Request,
) -> Response:
    """A one-page letter with the resume's header; nothing is stored."""
    contact = await _contact(request, principal.user_id)
    try:
        pdf = await asyncio.to_thread(
            compiler.build_letter_pdf,
            contact,
            item.text,
            date=item.date,
            company=item.company,
        )
    except PdfError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    return Response(content=pdf, media_type="application/pdf")
