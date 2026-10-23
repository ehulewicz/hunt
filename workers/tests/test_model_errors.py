from __future__ import annotations

import json

import pytest

from personal_os_worker.jobs.model_errors import ModelError, checked_output

PRIVATE = "the prompt said: my GPA is 3.78"


def failed(output: str, returncode: int) -> str:
    with pytest.raises(ModelError) as raised:
        checked_output(output, returncode)
    return str(raised.value)


def test_a_failure_names_its_codes_and_never_its_text() -> None:
    envelope = {"type": "result", "subtype": "error_during_execution", "is_error": True,
                "api_error": "invalid_request_error", "api_error_status": 400, "result": PRIVATE}
    message = failed(json.dumps(envelope), 1)
    assert message.startswith(
        "Claude call failed (exit 1, error_during_execution, invalid_request_error, HTTP 400)"
    )
    assert "GPA" not in message


def test_a_call_that_printed_no_envelope_says_so() -> None:
    assert "(exit 2, no JSON envelope)" in failed("error: " + PRIVATE, 2)


def test_a_field_that_is_not_a_code_is_left_out() -> None:
    envelope = {"is_error": True, "subtype": PRIVATE, "api_error": "Bad Thing", "result": "x"}
    assert "(an error envelope)" in failed(json.dumps(envelope), 0)


def test_the_usage_limit_keeps_its_own_message() -> None:
    envelope = {"is_error": True, "api_error": "usage_limit_reached", "api_error_status": 429}
    assert failed(json.dumps(envelope), 1).startswith("Claude usage limit reached")


def test_a_successful_envelope_passes_through() -> None:
    output = json.dumps({"is_error": False, "subtype": "success", "structured_output": {}})
    assert checked_output(output, 0) == output
