"""Sanitized fixtures: the live page as safe files a learned file may see.

Built from `page_evidence`: controls.json keeps structure but no values, and
the HTML loses everything the page held — text input and textarea values,
checked/selected/pressed state, and every personal fact text, replaced with a
neutral token. Secrets were already redacted by page_evidence.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Attributes carrying state nobody may replay out of a fixture.
_STATE_ATTRS = re.compile(
    r"\s(?:checked|selected|aria-checked|aria-pressed|aria-selected)"
    r'(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+))?',
    re.IGNORECASE,
)
_VALUE_ATTR = re.compile(
    r'(<(?:input|textarea)[^>]*?\s)value\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)',
    re.IGNORECASE,
)
NEUTRAL = "[name]"


def _fact_strings(facts: dict[str, Any]) -> set[str]:
    """Every personal text in the run's facts, recursively, 3+ characters."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if len(value.strip()) >= 3:
                found.add(value.strip())
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    for value in facts.values():
        walk(value)
    return found


def _shapes() -> list[re.Pattern[str]]:
    """Email and phone shapes, replaced even when no fact spells them out."""
    return [
        re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
        re.compile(r"\+?\d[\d\s().-]{7,}\d"),
    ]


def sanitize_evidence(
    evidence: dict[str, bytes], facts: dict[str, Any]
) -> dict[str, bytes]:
    """`page_evidence` output with values, state, and personal text removed."""
    secrets = sorted(_fact_strings(facts), key=len, reverse=True)
    shapes = _shapes()
    out: dict[str, bytes] = {}
    for name, data in evidence.items():
        if name == "controls.json":
            try:
                controls = json.loads(data)
                for control in controls:
                    for key in ("value", "selected", "filled"):
                        control.pop(key, None)
                data = json.dumps(controls, indent=2).encode()
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
            out[name] = data
            continue
        if name.endswith((".html", ".json")):
            text = data.decode("utf-8", "replace")
            text = _VALUE_ATTR.sub(r"\1value=\"\"", text)
            text = _STATE_ATTRS.sub("", text)
            for secret in secrets:
                text = text.replace(secret, NEUTRAL)
            for shape in shapes:
                text = shape.sub(NEUTRAL, text)
            data = text.encode()
        out[name] = data
    return out
