"""Terminal rendering and row references.

UUIDs are unusable in a terminal, so every table shows a row number and every
command that takes one resolves it against the database at that moment, the
way the table was just drawn from it. A listing's number is assigned by the
database over one canonical board, so it is the same in every listing view;
the other tables are small and numbered by position in one deterministic
query. An unambiguous id prefix works everywhere too, the way git resolves
short hashes. Nothing is cached on this machine.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

SHORT_ID_LENGTH = 8

console = Console()


class ReferenceError(RuntimeError):
    """Raised when a row reference cannot be resolved to exactly one record."""


def short_id(value: str) -> str:
    return value.replace("-", "")[:SHORT_ID_LENGTH]


def pick(items: list[dict[str, Any]], reference: str, *, kind: str) -> dict[str, Any]:
    """The one item a row number (1-based position) or id prefix denotes."""
    if reference.isdigit():
        index = int(reference)
        if not 1 <= index <= len(items):
            raise ReferenceError(f"Row {index} is out of range; there are {len(items)} {kind}.")
        return items[index - 1]
    normalized = reference.replace("-", "").casefold()
    if len(normalized) < 4:
        raise ReferenceError(f"Id prefix {reference!r} is too short; use at least 4 characters.")
    matches = [
        item
        for item in items
        if str(item.get("id", "")).replace("-", "").casefold().startswith(normalized)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ReferenceError(
            f"Id prefix {reference!r} matches {len(matches)} {kind}; use more characters."
        )
    raise ReferenceError(f"No {kind} match {reference!r}.")
