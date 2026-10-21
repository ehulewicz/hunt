"""Safe failure messages for the existing model call sites; never log their payloads."""

from __future__ import annotations

import json


class ModelError(RuntimeError):
    """The model did not perform the requested work."""


def checked_output(output: str, returncode: int) -> str:
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("api_error") == "usage_limit_reached":
        raise ModelError("Claude usage limit reached; drafting and answer checks unavailable")
    if returncode or payload.get("is_error"):
        raise ModelError("Claude call failed; drafting and answer checks unavailable")
    return output
