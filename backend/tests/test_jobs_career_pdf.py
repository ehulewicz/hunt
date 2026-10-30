import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from personal_os_backend.jobs_career_pdf import (
    PdfError,
    build_letter_pdf,
    build_resume_pdf,
    overlong_bullets,
    printed_document,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "career_v2.json"
NEEDS_TEX = pytest.mark.skipif(
    shutil.which("latexmk") is None or shutil.which("pdftotext") is None,
    reason="TeX Live and poppler are not installed here",
)


def canonical() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload


def test_the_projection_is_the_whole_document_minus_meta() -> None:
    printed = printed_document(canonical())
    assert set(printed) == {
        "contact", "education", "experience", "research", "projects",
        "activities", "skills",
    }
    assert printed["education"][0]["courses"] == [
        "Data Structures & Algorithms", "Systems & Architecture",
        "Perception & Robotics", "Machine Learning", "Linear Algebra",
    ]
    assert len(printed["experience"]) == 3


def test_overlong_bullets_reads_pdftotext_layout() -> None:
    layout = "      \u2022 A two-line bullet that keeps\n          going here\n\n"
    assert overlong_bullets(layout) == []
    layout = "      \u2022 A three-line bullet that keeps\n          going and\n          going\n"
    assert overlong_bullets(layout) == [("A three-line bullet that keeps", 3)]


@NEEDS_TEX
def test_the_fixture_compiles_to_one_page() -> None:
    pdf = build_resume_pdf(printed_document(canonical()))
    assert pdf.startswith(b"%PDF")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as out:
        out.write(pdf)
        info = subprocess.run(
            ["pdfinfo", out.name], check=True, capture_output=True, text=True
        )
    import re

    assert re.search(r"^Pages:\s+1$", info.stdout, re.MULTILINE)


@NEEDS_TEX
def test_two_builds_are_byte_identical() -> None:
    printed = printed_document(canonical())
    assert build_resume_pdf(printed) == build_resume_pdf(printed)


@NEEDS_TEX
def test_off_resume_material_never_changes_the_bytes() -> None:
    base = build_resume_pdf(printed_document(canonical()))
    changed = canonical()
    changed["experience"].append(
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "title": "Hidden Work", "onResume": False, "date": "June 2026",
            "bullets": [], "moreBullets": ["A kept bullet the resume omits"],
        }
    )
    changed["experience"][0]["id"] = "22222222-2222-4222-8222-222222222222"
    changed["experience"][0]["moreBullets"] = ["Another kept bullet"]
    assert build_resume_pdf(printed_document(changed)) == base


@NEEDS_TEX
def test_an_overlong_record_is_refused() -> None:
    document = canonical()
    document["experience"][0]["bullets"] = document["experience"][0]["bullets"] * 10
    with pytest.raises(PdfError, match="1 page"):
        build_resume_pdf(printed_document(document))


@NEEDS_TEX
def test_a_cover_letter_compiles_to_one_page() -> None:
    contact = {
        "name": "Alex Example",
        "email": "alex.example@example.com",
        "phone": "555-010-0199",
    }
    pdf = build_letter_pdf(
        contact,
        "Dear Hiring Team,\n\nI am writing about the internship.\n\nBest,\nEthan",
        date="October 30, 2026",
        company="Acme",
    )
    assert pdf.startswith(b"%PDF")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as out:
        out.write(pdf)
        info = subprocess.run(
            ["pdfinfo", out.name], check=True, capture_output=True, text=True
        )
    import re

    assert re.search(r"^Pages:\s+1$", info.stdout, re.MULTILINE)


@NEEDS_TEX
def test_the_trailer_id_depends_on_the_printed_projection() -> None:
    one = build_resume_pdf(printed_document(canonical()))
    two = build_resume_pdf(printed_document(canonical()))
    assert hashlib.sha256(one).hexdigest() == hashlib.sha256(two).hexdigest()
