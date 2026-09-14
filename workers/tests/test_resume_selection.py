import json
from typing import Any

from personal_os_worker.resume_selection import requires_later_graduation


def scripted(payload: dict[str, Any]) -> Any:
    def runner(argv: list[str]) -> str:
        return json.dumps(payload)

    return runner


def test_a_high_confidence_requirement_switches_the_resume() -> None:
    description = "Candidates must graduate after December 2027 to be eligible."
    runner = scripted({"requires_later_graduation": True, "confidence": "high"})
    assert requires_later_graduation(description, runner=runner) is True


def test_low_confidence_keeps_the_canonical_resume() -> None:
    description = "A fairly typical internship posting."
    runner = scripted({"requires_later_graduation": True, "confidence": "low"})
    assert requires_later_graduation(description, runner=runner) is False


def test_no_stated_requirement_keeps_the_canonical_resume() -> None:
    description = "We are looking for a software engineering intern."
    runner = scripted({"requires_later_graduation": False, "confidence": "high"})
    assert requires_later_graduation(description, runner=runner) is False


def test_malformed_output_keeps_the_canonical_resume() -> None:
    def runner(argv: list[str]) -> str:
        return "not json"

    assert requires_later_graduation("anything", runner=runner) is False


def test_empty_description_never_calls_the_runner() -> None:
    def runner(argv: list[str]) -> str:
        raise AssertionError("runner should not be called for an empty description")

    assert requires_later_graduation(None, runner=runner) is False
    assert requires_later_graduation("   ", runner=runner) is False
