from personal_os_worker.jobs.grad_date import variants


def test_with_graduation_patches_the_primary_school_only() -> None:
    original = {
        "education": [
            {"school": "Example University", "primary": True, "graduation": "December 2027"},
            {"school": "High School", "primary": False, "graduation": "June 2024"},
        ],
        "other": 1,
    }
    patched = variants.with_graduation(original, variants.LATER_GRADUATION)
    assert patched["education"][0]["graduation"] == variants.LATER_GRADUATION
    assert patched["education"][1]["graduation"] == "June 2024"
    # The original document is not mutated.
    assert original["education"][0]["graduation"] == variants.CANONICAL_GRADUATION


def test_with_graduation_tolerates_a_document_without_a_primary() -> None:
    patched = variants.with_graduation({"education": [{"school": "S"}]}, "May 2028")
    assert patched["education"] == [{"school": "S"}]
