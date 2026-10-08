import json
import time
from pathlib import Path

import pytest

from personal_os_worker.documents import resume
from personal_os_worker.jobs.grad_date import variants


def valid_resume() -> str:
    return """{
      "contact": {"name": "Candidate"},
      "education": {"school": "School"},
      "experience": [{"title": "Engineer", "bullets": ["Built it.", "Ran it.", "Shipped it."]}],
      "research": [],
      "projects": [],
      "skills": [{"category": "Languages", "items": "Python"}]
    }"""


def test_later_graduation_resume_is_cached_and_rebuilt_when_the_source_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from personal_os_worker.core import service

    source = tmp_path / "main.json"
    source.write_text(valid_resume())
    monkeypatch.setenv("PERSONAL_OS_RESUME_JSON", str(source))
    state = tmp_path / "state"
    monkeypatch.setattr(service, "state_dir", lambda: state)
    seen_graduations: list[str] = []

    def fake_compile(payload: dict[str, object], directory: Path) -> Path:
        seen_graduations.append(payload["education"]["graduation"])  # type: ignore[index]
        (directory / "resume.tex").write_text("latex")
        pdf = directory / "resume.pdf"
        pdf.write_bytes(b"%PDF-later")
        return pdf

    monkeypatch.setattr(resume, "compile_resume", fake_compile)

    first = variants.later_graduation_resume_pdf()
    assert first == state / "resume-variants" / "later-graduation.pdf"
    assert first.read_bytes() == b"%PDF-later"
    assert seen_graduations == [variants.LATER_GRADUATION]

    # Unchanged source: cached, no recompile.
    again = variants.later_graduation_resume_pdf()
    assert again == first and seen_graduations == [variants.LATER_GRADUATION]

    # The canonical source on disk is untouched by the patch.
    assert "graduation" not in json.loads(source.read_text())["education"]

    # A newer source triggers a rebuild.
    time.sleep(0.01)
    source.write_text(valid_resume())
    rebuilt = variants.later_graduation_resume_pdf()
    assert rebuilt == first and seen_graduations == [variants.LATER_GRADUATION] * 2


def test_with_graduation_replaces_only_the_graduation_field() -> None:
    original = {"education": {"school": "School", "graduation": "May 2028"}, "other": 1}
    patched = variants.with_graduation(original, "December 2027")
    assert patched == {"education": {"school": "School", "graduation": "December 2027"}, "other": 1}
    assert original["education"]["graduation"] == "May 2028"  # not mutated
