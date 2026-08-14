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


def two_bullet_resume() -> dict[str, object]:
    first = " ".join(["built"] + [f"system{index}" for index in range(1, 100)])
    second = " ".join(["shipped"] + [f"service{index}" for index in range(1, 100)])
    base = resume()
    base["experience"][0]["bullets"] = [first, second]  # type: ignore[index]
    return base


def test_inflected_form_of_an_approved_fact_is_allowed() -> None:
    """The resume says 'built'; 'build' is the same fact, not a new claim."""
    base = resume()
    original = str(base["experience"][0]["bullets"][0])  # type: ignore[index]
    replacement = original.replace("system99", "build")

    result = apply_and_validate(
        base,
        [{"op": "replace", "path": "/experience/0/bullets/0", "value": replacement}],
        job_description="You will build reliable systems.",
        fact_context="The candidate built internal systems.",
    )

    assert result.changed_paths == ("/experience/0/bullets/0",)
    assert result.skipped == ()


def test_ungrounded_patch_is_skipped_without_failing_the_task() -> None:
    """One inflated keyword must not discard the other valid patches."""
    base = two_bullet_resume()
    bullets = base["experience"][0]["bullets"]  # type: ignore[index]
    grounded = str(bullets[0]).replace("system99", "build")
    inflated = str(bullets[1]).replace("service99", "pipelines")

    result = apply_and_validate(
        base,
        [
            {"op": "replace", "path": "/experience/0/bullets/0", "value": grounded},
            {"op": "replace", "path": "/experience/0/bullets/1", "value": inflated},
        ],
        job_description="You will build data pipelines.",
        fact_context="The candidate built internal systems.",
    )

    assert result.changed_paths == ("/experience/0/bullets/0",)
    assert len(result.skipped) == 1
    assert "pipelines" in result.skipped[0]
    assert result.candidate["experience"][0]["bullets"][1] == str(bullets[1])


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
