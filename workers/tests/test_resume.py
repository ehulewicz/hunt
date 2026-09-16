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
      "research": [
        {"title": "Researcher", "bullets": ["Studied it.", "Tested it.", "Reported it."]}
      ],
      "projects": [{"title": "Project", "bullets": ["Built it.", "Ran it.", "Shipped it."]}],
      "skills": [{"category": "Languages", "items": "Python"}]
    }"""


def test_default_resume_artifacts_live_together_in_the_repository() -> None:
    assert resume.canonical_resume_path().parts[-3:] == ("artifacts", "career", "main.json")
    assert resume.default_pdf_path().parts[-3:] == ("artifacts", "career", "Alex_Example.pdf")
    assert resume.transcript_pdf_path() is not None
    assert resume.transcript_pdf_path().parts[-3:] == (
        "artifacts",
        "career",
        "Transcript.pdf",
    )


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


def test_template_pins_the_reference_font_family_and_size_hierarchy() -> None:
    tex = build_latex({"contact": {"name": "Candidate"}})

    assert r"\documentclass[letterpaper,11pt]{article}" in tex
    assert r"\usepackage{newtxtext,newtxmath}" in tex
    assert r"\textbf{\Huge \scshape Candidate}" in tex
    assert r"\titleformat{\section}" in tex


def test_load_resume_rejects_invalid_json_with_location(tmp_path: Path) -> None:
    source = tmp_path / "main.json"
    source.write_text('{"contact":')
    with pytest.raises(RuntimeError, match=r"line 1, column 12"):
        resume.load_resume(source)


def test_load_resume_accepts_blank_bullet_slots_and_variable_counts(tmp_path: Path) -> None:
    source = tmp_path / "main.json"
    source.write_text(
        """{
          "contact": {"name": "Candidate"},
          "education": {"school": "School"},
          "experience": [{"title": "Engineer", "bullets": ["", "", "", ""]}],
          "research": [{"title": "Researcher", "bullets": ["", "", ""]}],
          "projects": [{"title": "Project", "bullets": ["", "", ""]}],
          "skills": [{"category": "Languages", "items": "Python"}]
        }"""
    )

    loaded = resume.load_resume(source)

    assert loaded["experience"][0]["bullets"] == ["", "", "", ""]
    assert loaded["research"][0]["bullets"] == ["", "", ""]


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


def test_ensure_resume_compiles_only_when_the_tracked_pdf_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "main.json"
    source.write_text(valid_resume())
    monkeypatch.setenv("PERSONAL_OS_RESUME_JSON", str(source))
    compiles: list[Path] = []

    def fake_compile(_payload: object, directory: Path) -> Path:
        compiles.append(directory)
        (directory / "resume.tex").write_text("latex")
        pdf = directory / "resume.pdf"
        pdf.write_bytes(b"%PDF-built")
        return pdf

    monkeypatch.setattr(resume, "compile_resume", fake_compile)

    first = resume.ensure_resume()
    assert first.pdf == tmp_path / resume.RESUME_PDF_NAME
    assert first.pdf.read_bytes() == b"%PDF-built" and len(compiles) == 1

    first.pdf.write_bytes(b"%PDF-edited-by-hand")
    again = resume.ensure_resume()
    assert again.pdf == first.pdf
    assert again.pdf.read_bytes() == b"%PDF-edited-by-hand" and len(compiles) == 1


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


def test_bullet_markup_stays_literal_and_escaped() -> None:
    """Bullets carry no keyword emphasis, so asterisks are ordinary characters.

    The template has no keyword macro, and the resume rules put technologies in
    the Skills section rather than bolding them inline.
    """
    tex = build_latex(
        {"projects": [{"title": "Project", "bullets": ["Used **C++ & Redis** daily."]}]}
    )

    bullet = next(line for line in tex.splitlines() if "Redis" in line)
    assert bullet == r"        \resumeItem{Used **C++ \& Redis** daily.}"


def test_bullet_spacing_sits_outside_the_text_line() -> None:
    """A full-width bullet must not wrap the spacing command onto a blank line."""
    tex = build_latex({"projects": [{"title": "Project", "bullets": ["Filled line."]}]})

    assert r"\item\small{#1}\vspace{-2pt}" in tex
    assert r"#1 \vspace{-2pt}" not in tex


def test_empty_skills_list_omits_the_section() -> None:
    assert r"\section{Technical Skills}" not in build_latex({"skills": []})
    populated = build_latex({"skills": [{"category": "Languages", "items": "Python"}]})
    assert r"\section{Technical Skills}" in populated


def test_research_renders_between_experience_and_projects() -> None:
    latex = build_latex(
        {
            "experience": [{"title": "Engineer", "bullets": [""]}],
            "research": [{"title": "Researcher", "bullets": [""]}],
            "projects": [{"title": "Project", "bullets": [""]}],
        }
    )

    assert latex.index(r"\section{Experience}") < latex.index(r"\section{Research}")
    assert latex.index(r"\section{Research}") < latex.index(r"\section{Projects}")
    assert latex.count(r"\resumeItem{}") == 3


def test_education_renders_location_and_graduation_like_the_reference_template() -> None:
    latex = build_latex(
        {
            "education": {
                "school": "Example University",
                "location": "Atlanta, GA",
                "degree": "BS Computer Science",
                "graduation": "May 2028",
            }
        }
    )

    assert r"{Example University}{Atlanta, GA}" in latex
    assert r"\textit{\small BS Computer Science} & \textit{\small May 2028} \\" in latex


def test_education_renders_one_row_per_declared_degree() -> None:
    latex = build_latex(
        {
            "education": {
                "school": "Example University",
                "location": "Atlanta, GA",
                "degrees": [
                    {"degree": "MS Computer Science", "date": "Dec 2027"},
                    {"degree": "BS Computer Science", "date": "May 2026"},
                ],
                "courses": "Algorithms",
            }
        }
    )

    assert r"\textit{\small MS Computer Science} & \textit{\small Dec 2027} \\" in latex
    assert r"\textit{\small BS Computer Science} & \textit{\small May 2026} \\" in latex
    assert r"\small Core Coursework: Algorithms & \\" in latex


def test_entry_dates_keep_an_en_dash_range() -> None:
    entry = {"title": "Project", "date": "2026 \u2013 Present", "bullets": ["Shipped it."]}
    tex = build_latex({"projects": [entry]})

    assert r"\resumeProjectHeading{\textbf{Project}}{2026 -- Present}" in tex


def test_project_awards_sit_below_the_project_name() -> None:
    entry = {"title": "Project", "org": "Hackathon Winner", "date": "2026", "bullets": ["Did it."]}
    tex = build_latex({"projects": [entry]})

    assert r"\resumeSubheading{Project}{2026}{Hackathon Winner}{}" in tex


def test_experience_entries_lead_with_the_role_like_the_reference_template() -> None:
    entry = {"title": "Engineer", "org": "Company", "location": "Atlanta, GA", "date": "2025"}
    tex = build_latex({"experience": [entry | {"bullets": ["Did it."]}]})

    assert r"\resumeSubheading{Engineer}{2025}{Company}{Atlanta, GA}" in tex


def test_entry_qualifier_is_italicized_before_bar_and_date() -> None:
    entry = {
        "title": "Engineer",
        "org": "Company",
        "location": "Atlanta, GA",
        "qualifier": "Continuous Employment",
        "date": "May 2025 \u2013 Present",
        "bullets": ["Did it."],
    }
    tex = build_latex({"experience": [entry]})

    assert (
        r"\resumeSubheading{Engineer}{\textit{Continuous Employment} $|$ May 2025 -- Present}"
        r"{Company}{Atlanta, GA}"
    ) in tex


def test_sections_without_entries_emit_nothing() -> None:
    """An absent section leaves no label and no rule behind.

    This is what lets one generator serve a resume with Research and no
    Leadership and the reference resume, which is the other way round.
    """
    latex = build_latex(
        {
            "experience": [{"title": "Engineer", "bullets": ["Did it."]}],
            "research": [],
            "leadership": [{"title": "Teaching Assistant", "bullets": ["Taught it."]}],
        }
    )

    assert r"\section{Research}" not in latex
    assert r"\section{Projects}" not in latex
    assert latex.index(r"\section{Experience}") < latex.index(r"\section{Leadership}")


def test_skills_render_as_one_item_of_joined_lines() -> None:
    """Every category shares a single list item, separated by line breaks.

    One item per category would inherit the list's inter-item spacing and open
    the block up; the reference keeps the categories tight.
    """
    latex = build_latex(
        {
            "skills": [
                {"category": "Languages", "items": "Python"},
                {"category": "Libraries", "items": "NumPy"},
            ]
        }
    )

    assert latex.count(r"\item{") == 1
    assert r"\textbf{Languages}{: Python} \\" in latex
    assert r"\textbf{Libraries}{: NumPy}" in latex
