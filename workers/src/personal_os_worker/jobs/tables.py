"""Jobs row rendering.

The listing, application and answer tables are Jobs-shaped: they know about
ATS form types and posting age. `core.tables` keeps only the pieces every
domain needs — the console, row references and short ids.
"""

from __future__ import annotations

from typing import Any

from rich import box
from rich.table import Table
from rich.text import Text

from personal_os_worker.core.tables import console, short_id
from personal_os_worker.jobs.adapters import detect_ats


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
