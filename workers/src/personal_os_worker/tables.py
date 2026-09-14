"""Terminal rendering and row references.

UUIDs are unusable in a terminal, so every table caches what it rendered and
accepts either a row number from that cache or an unambiguous id prefix, the way
git resolves short hashes. Row numbers make batch commands practical; prefixes
keep working when the cache is stale or missing.
"""

from __future__ import annotations

import json
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from personal_os_worker.service import state_dir

SHORT_ID_LENGTH = 8

console = Console()


class ReferenceError(RuntimeError):
    """Raised when a row reference cannot be resolved to exactly one record."""


def short_id(value: str) -> str:
    return value.replace("-", "")[:SHORT_ID_LENGTH]


def cache_path(kind: str) -> str:
    return str(state_dir() / f"last-{kind}.json")


def remember(
    kind: str, ids: list[str], *, records: list[dict[str, Any]] | None = None
) -> None:
    path = state_dir() / f"last-{kind}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"ids": ids}
    if records is not None:
        payload["records"] = records
    path.write_text(json.dumps(payload, indent=2) + "\n")


def recall(kind: str) -> list[str]:
    try:
        data = json.loads((state_dir() / f"last-{kind}.json").read_text())
        ids = data["ids"]
        return [str(value) for value in ids] if isinstance(ids, list) else []
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return []


def resolve(kind: str, reference: str) -> str:
    """Resolve a row number or id prefix to exactly one id."""
    known = recall(kind)
    if reference.isdigit():
        index = int(reference)
        if not known:
            raise ReferenceError(
                f"No cached {kind} to number against. Run 'os hunt {kind}' first, "
                "or pass an id prefix."
            )
        if not 1 <= index <= len(known):
            raise ReferenceError(
                f"Row {index} is out of range; the last {kind} result had {len(known)} rows."
            )
        return known[index - 1]

    normalized = reference.replace("-", "").casefold()
    if len(normalized) < 4:
        raise ReferenceError(f"Id prefix {reference!r} is too short; use at least 4 characters.")
    matches = [value for value in known if value.replace("-", "").casefold().startswith(normalized)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ReferenceError(
            f"Id prefix {reference!r} matches {len(matches)} rows; use more characters."
        )
    raise ReferenceError(
        f"Nothing in the last {kind} result matches {reference!r}. Run 'os hunt {kind}' to refresh."
    )


def resolve_record(kind: str, reference: str) -> dict[str, Any]:
    """Resolve a displayed row and return its cached non-secret display data."""
    record_id = resolve(kind, reference)
    try:
        data = json.loads((state_dir() / f"last-{kind}.json").read_text())
        records = data.get("records")
    except (FileNotFoundError, TypeError, ValueError, json.JSONDecodeError):
        records = None
    if isinstance(records, list):
        for record in records:
            if isinstance(record, dict) and str(record.get("id")) == record_id:
                return record
    raise ReferenceError(
        f"The cached {kind} rows predate link support. Run 'os hunt {kind}' to refresh."
    )


def _age(days: Any) -> str:
    if not isinstance(days, int):
        return "-"
    if days == 0:
        return "today"
    return f"{days}d"


def render_listings(items: list[dict[str, Any]], total: int) -> None:
    # One row must stay one line, or the table stops being scannable.
    table = Table(
        title=f"Listings ({len(items)} of {total})",
        header_style="bold",
        box=box.SIMPLE_HEAD,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("company", no_wrap=True, overflow="ellipsis")
    table.add_column("title", no_wrap=True, overflow="ellipsis", ratio=1)
    table.add_column("location", no_wrap=True, overflow="ellipsis")
    table.add_column("age", justify="right", no_wrap=True)
    table.add_column("cat", no_wrap=True)
    table.add_column("app", no_wrap=True)

    for index, item in enumerate(items, start=1):
        category = str(item.get("source_category") or "")
        title = str(item.get("title") or "-")
        application_url = str(item.get("application_url") or "")
        rendered_title = (
            Text(title, style=f"link {application_url}") if application_url else Text(title)
        )
        table.add_row(
            str(index),
            short_id(str(item.get("id", ""))),
            str(item.get("company_name") or "-"),
            rendered_title,
            str(item.get("location") or "-"),
            _age(item.get("source_age_days")),
            "" if category == "other" else category,
            "yes" if item.get("application_id") else "",
        )
    console.print(table)
    remember(
        "listings",
        [str(item.get("id", "")) for item in items],
        records=[
            {
                "id": str(item.get("id", "")),
                "company_name": item.get("company_name"),
                "title": item.get("title"),
                "application_url": item.get("application_url"),
                "discovery_url": item.get("discovery_url"),
            }
            for item in items
        ],
    )


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
    remember("apps", [str(item.get("id", "")) for item in items])


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
    remember("answers", [str(item.get("id", "")) for item in items])
