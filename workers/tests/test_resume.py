from pathlib import Path

import httpx
import pytest

from personal_os_worker import resume
from personal_os_worker.resume_pdf import REPRODUCIBLE_EPOCH, build_latex, latex_environment


def valid_resume() -> str:
    return """{
      "contact": {"name": "Candidate"},
      "education": {"school": "School"},
      "experience": [{"title": "Engineer", "bullets": ["Built it.", "Ran it.", "Shipped it."]}],
      "projects": [{"title": "Project", "bullets": ["Built it.", "Ran it.", "Shipped it."]}],
      "skills": [{"category": "Languages", "items": "Python"}]
    }"""


def test_default_resume_artifacts_live_together_in_the_repository() -> None:
    assert resume.canonical_resume_path().parts[-3:] == ("artifacts", "resume", "main.json")
    assert resume.default_pdf_path() == resume.canonical_resume_path().with_suffix(".pdf")


def test_latex_build_environment_is_reproducible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "different")

    environment = latex_environment()

    assert environment["SOURCE_DATE_EPOCH"] == REPRODUCIBLE_EPOCH
    assert environment["FORCE_SOURCE_DATE"] == "1"


def test_latex_trailer_id_is_content_derived() -> None:
    first = build_latex({"contact": {"name": "Candidate"}})
    second = build_latex({"contact": {"name": "Candidate"}})
    changed = build_latex({"contact": {"name": "Different"}})

    first_id = next(line for line in first.splitlines() if line.startswith("\\pdftrailerid"))
    assert first_id == next(
        line for line in second.splitlines() if line.startswith("\\pdftrailerid")
    )
    assert first_id not in changed


def test_load_resume_rejects_invalid_json_with_location(tmp_path: Path) -> None:
    source = tmp_path / "main.json"
    source.write_text('{"contact":')
    with pytest.raises(RuntimeError, match=r"line 1, column 12"):
        resume.load_resume(source)


def test_build_resume_atomically_publishes_pdf_and_tex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "main.json"
    source.write_text(valid_resume())
    output = tmp_path / "published" / "main.pdf"

    def fake_compile(_payload: object, directory: Path) -> Path:
        (directory / "resume.tex").write_text("latex")
        pdf = directory / "resume.pdf"
        pdf.write_bytes(b"%PDF-test")
        return pdf

    monkeypatch.setattr(resume, "compile_resume", fake_compile)
    result = resume.build_resume(source, output)

    assert result.pdf == output.resolve()
    assert output.read_bytes() == b"%PDF-test"
    assert output.with_suffix(".tex").read_text() == "latex"
    assert not list(output.parent.glob("*.tmp"))


def test_preview_server_serves_pdf_and_auto_refresh_shell(tmp_path: Path) -> None:
    pdf = tmp_path / "main.pdf"
    pdf.write_bytes(b"%PDF-preview")
    try:
        with resume._PreviewServer(pdf) as preview:
            page = httpx.get(preview.url)
            rendered = httpx.get(f"{preview.url}resume.pdf")
            version = httpx.get(f"{preview.url}version")
    except PermissionError:
        pytest.skip("sandbox does not permit binding a loopback preview server")

    assert page.status_code == 200
    assert "setInterval" in page.text and "/version" in page.text
    assert rendered.content == b"%PDF-preview"
    assert version.text != "missing"


def test_watch_rejects_non_positive_interval() -> None:
    with pytest.raises(RuntimeError, match="greater than zero"):
        resume.watch_resume(interval=0)


def test_keyword_markup_renders_after_escaping() -> None:
    tex = build_latex(
        {"projects": [{"title": "Project", "bullets": ["Used **C++ & Redis** daily."]}]}
    )

    assert r"\resumeKeyword{C++ \& Redis}" in tex


def test_unpaired_keyword_markers_stay_literal() -> None:
    tex = build_latex({"projects": [{"title": "Project", "bullets": ["Shipped **half a marker."]}]})

    bullet = next(line for line in tex.splitlines() if "half a marker" in line)
    assert bullet == r"    \resumeItem{Shipped **half a marker.}"


def test_empty_skills_list_omits_the_section() -> None:
    assert r"\section{Skills}" not in build_latex({"skills": []})
    populated = build_latex({"skills": [{"category": "Languages", "items": "Python"}]})
    assert r"\section{Skills}" in populated


def test_entry_dates_keep_an_en_dash_range() -> None:
    entry = {"title": "Project", "date": "2026 \u2013 Present", "bullets": ["Shipped it."]}
    tex = build_latex({"projects": [entry]})

    assert r"\resumeHeading{Project}{2026 -- Present}" in tex


def test_project_awards_sit_below_the_project_name() -> None:
    entry = {"title": "Project", "org": "Hackathon Winner", "date": "2026", "bullets": ["Did it."]}
    tex = build_latex({"projects": [entry]})

    assert r"\resumeEntry{Project}{2026}{Hackathon Winner}{}" in tex


def test_experience_entries_lead_with_the_organization() -> None:
    entry = {"title": "Engineer", "org": "Company", "location": "Atlanta, GA", "date": "2025"}
    tex = build_latex({"experience": [entry | {"bullets": ["Did it."]}]})

    assert r"\resumeEntry{Company}{Atlanta, GA}{Engineer}{2025}" in tex
