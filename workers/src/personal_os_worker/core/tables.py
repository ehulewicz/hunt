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

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from personal_os_worker.jobs.adapters import detect_ats

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


def _ats(application_url: str) -> str:
    """The form type behind the direct URL; blank when it is nothing we recognize."""
    name = detect_ats(application_url).name
    return "" if name == "generic" else name


def _age(days: Any) -> str:
    if not isinstance(days, int):
        return "-"
    if days == 0:
        return "today"
    return f"{days}d"


def render_listings(items: list[dict[str, Any]], total: int, *, title: str = "Listings") -> None:
    """Rows carry the board number the database assigned."""
    # One row must stay one line, or the table stops being scannable.
    table = Table(
        title=f"{title} ({len(items)} of {total})",
        header_style="bold",
        box=box.SIMPLE_HEAD,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("company", no_wrap=True, overflow="ellipsis")
    table.add_column("title", no_wrap=True, overflow="ellipsis", ratio=1)
    table.add_column("location", no_wrap=True, overflow="ellipsis")
    table.add_column("age", justify="right", no_wrap=True)
    table.add_column("ats", no_wrap=True, style="dim")
    table.add_column("cat", no_wrap=True)

    for item in items:
        category = str(item.get("source_category") or "")
        title_text = str(item.get("title") or "-")
        application_url = str(item.get("application_url") or "")
        rendered_title = (
            Text(title_text, style=f"link {application_url}")
            if application_url
            else Text(title_text)
        )
        table.add_row(
            str(item.get("row") or "-"),
            str(item.get("company_name") or "-"),
            rendered_title,
            str(item.get("location") or "-"),
            _age(item.get("source_age_days")),
            _ats(application_url),
            "" if category == "other" else category,
        )
    console.print(table)


def render_applications(items: list[dict[str, Any]], *, title: str = "Applications") -> None:
    table = Table(
        title=f"{title} ({len(items)})",
        header_style="bold",
        box=box.SIMPLE_HEAD,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("company", no_wrap=True, overflow="ellipsis")
    table.add_column("title", no_wrap=True, overflow="ellipsis", ratio=1)
    table.add_column("status", no_wrap=True)
    table.add_column("applied", no_wrap=True)

    for index, item in enumerate(items, start=1):
        applied = str(item.get("applied_at") or "")[:10]
        table.add_row(
            str(index),
            short_id(str(item.get("id", ""))),
            str(item.get("company_name") or "-"),
            str(item.get("title") or "-"),
            str(item.get("status") or "-").replace("_", " "),
            applied or "-",
        )
    console.print(table)


def render_answers(items: list[dict[str, Any]]) -> None:
    table = Table(
        title=f"Reusable answers ({len(items)})",
        header_style="bold",
        box=box.SIMPLE_HEAD,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("question", no_wrap=True, overflow="ellipsis", ratio=1)
    table.add_column("answer", overflow="ellipsis", ratio=2)
    table.add_column("state", no_wrap=True)
    table.add_column("reuse", no_wrap=True)
    for index, item in enumerate(items, start=1):
        table.add_row(
            str(index),
            short_id(str(item.get("id", ""))),
            str(item.get("question") or "-"),
            str(item.get("answer_text") or "-"),
            "approved" if item.get("approved") is True else "draft",
            str(item.get("reuse_policy") or "exact"),
        )
    console.print(table)
