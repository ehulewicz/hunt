import pytest

from personal_os_worker.resume_guard import UnsafeResumeChange, apply_and_validate


def resume() -> dict[str, object]:
    long_bullet = " ".join(["built"] + [f"system{index}" for index in range(1, 100)])
    return {
        "meta": {"slug": "main"},
        "contact": {"name": "Candidate"},
        "education": {"school": "School"},
        "experience": [
            {"title": "Engineer", "org": "Company", "date": "2025", "bullets": [long_bullet]}
        ],
        "projects": [],
        "skills": [{"category": "Tools", "items": "Python, PostgreSQL"}],
    }


def test_minimal_supported_keyword_patch_is_allowed() -> None:
    base = resume()
    original = base["experience"][0]["bullets"][0]  # type: ignore[index]
    replacement = str(original).replace("system99", "scalable")

    result = apply_and_validate(
        base,
        [{"op": "replace", "path": "/experience/0/bullets/0", "value": replacement}],
        job_description="We need scalable systems.",
        fact_context="The candidate built a scalable project.",
    )

    assert result.changed_word_ratio <= 0.10
    assert result.changed_paths == ("/experience/0/bullets/0",)


def test_structural_or_wholesale_changes_are_rejected() -> None:
    base = resume()
    with pytest.raises(UnsafeResumeChange, match="not allowed"):
        apply_and_validate(
            base,
            [{"op": "replace", "path": "/education/school", "value": "Other"}],
            job_description="Other",
            fact_context="Other",
        )

    with pytest.raises(UnsafeResumeChange, match="maximum"):
        apply_and_validate(
            base,
            [{
                "op": "replace",
                "path": "/experience/0/bullets/0",
                "value": " ".join(f"different{index}" for index in range(100)),
            }],
            job_description=" ".join(f"different{index}" for index in range(100)),
            fact_context=" ".join(f"different{index}" for index in range(100)),
        )
