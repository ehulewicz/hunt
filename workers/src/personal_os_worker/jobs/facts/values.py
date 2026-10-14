"""Fact rows as plain values."""

from __future__ import annotations

from typing import Any


def fact_values(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Fact rows as `{key: value}`."""
    return {str(fact["key"]): fact.get("value") for fact in facts if fact.get("key")}
