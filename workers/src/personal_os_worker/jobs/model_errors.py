"""Safe failure messages for the existing model call sites; never log their payloads."""

from __future__ import annotations

import json
import re

# An envelope field shown in a failure is a code, never text: the error's
# `result` and stderr can carry the prompt or the model's words.
CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ModelError(RuntimeError):
    """The model did not perform the requested work."""


class UsageLimit(ModelError):
    """Claude explicitly reported a usage or rate limit; another provider may try."""


def checked_output(output: str, returncode: int) -> str:
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        payload = None
    envelope = payload if isinstance(payload, dict) else {}
    limited = envelope.get("api_error") in ("usage_limit_reached", "rate_limit_error")
    if returncode or envelope.get("is_error"):
        limited |= envelope.get("api_error_status") == 429
        result = envelope.get("result")
        if isinstance(result, str):
            limited |= result.startswith(("You've hit your limit", "You\u2019ve hit your limit"))
    if limited:
        raise UsageLimit("Claude usage limit reached; drafting and answer checks unavailable")
    if returncode or envelope.get("is_error"):
        raise ModelError(
            f"Claude call failed ({failure_reason(payload, returncode)}); "
            "drafting and answer checks unavailable"
        )
    return output


def failure_reason(payload: object, returncode: int) -> str:
    """Why a call failed, from the exit code and the envelope's codes alone."""
    if not isinstance(payload, dict):
        return f"exit {returncode}, no JSON envelope"
    parts = [f"exit {returncode}"] if returncode else []
    for name in ("subtype", "api_error"):
        value = payload.get(name)
        if isinstance(value, str) and value != "success" and CODE.fullmatch(value):
            parts.append(value)
    status = payload.get("api_error_status")
    if isinstance(status, int) and not isinstance(status, bool):
        parts.append(f"HTTP {status}")
    return ", ".join(parts) or "an error envelope"
