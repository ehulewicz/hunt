"""What the system knows, and what the old one knew.

`os hunt patterns` shows the loop's state: which questions fill
deterministically (`active`), and which are waiting in the Inbox (`candidate`).
`os hunt replay` replays the code as it stands against the answers you
accepted, the check a code change passes before it is kept. `os hunt prose`
manages saved long-form writing. `os hunt archive` reads the frozen old system:
its answers, its retired rules with their translations, and its long-form
writing as Markdown.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from rich import box
from rich.markup import escape
from rich.table import Table

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.auth import LoginError
from personal_os_worker.core.commands import connected
from personal_os_worker.core.tables import console, pick, short_id
from personal_os_worker.jobs.archive.lookup import translate
from personal_os_worker.jobs.archive.rules import RULES
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.repair.commands import knowledge_for
from personal_os_worker.jobs.replay import Replay, replay_code, sightings_of

STATUS_STYLES = {"active": "green", "candidate": "yellow", "rejected": "red", "retired": "dim"}
# Archived answers that are writing rather than a fact: a contextual row, or a long one.
PROSE_LENGTH = 80


def table(title: str) -> Table:
    return Table(
        title=title, header_style="bold", box=box.SIMPLE_HEAD, padding=(0, 1), expand=True
    )


def render_patterns(patterns: list[dict[str, Any]]) -> None:
    view = table(f"Patterns ({len(patterns)})")
    view.add_column("status", no_wrap=True)
    view.add_column("question or expression", overflow="ellipsis", ratio=3)
    view.add_column("fact", no_wrap=True, ratio=2)
    view.add_column("from", no_wrap=True, style="dim")
    for pattern in patterns:
        status = str(pattern.get("status"))
        style = STATUS_STYLES.get(status, "")
        subject = (
            str(pattern.get("question_label") or "")
            if pattern.get("kind") == "binding"
            else f"/{pattern.get('expression')}/"
        )
        source = str(pattern.get("archive_ref") or pattern.get("origin") or "")
        view.add_row(
            f"[{style}]{status}[/{style}]" if style else status,
            escape(subject),
            str(pattern.get("fact_key")),
            escape(source.removeprefix("archive:")),
        )
    console.print(view)


def run_patterns(*, every: bool = False) -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        statuses = (
            ("active", "candidate", "repaired", "validated", "rejected", "retired")
            if every
            else ("active", "candidate")
        )
        patterns = api.patterns(*statuses)
    finally:
        api.close()
    if not patterns:
        console.print(
            "Nothing learned yet. Apply to something; the Inbox ('os hunt inbox') is where "
            "proposals become patterns."
        )
        return 0
    render_patterns(patterns)
    return 0


def code_replay(
    api: WorkerApi, *, question: str | None = None, fact_key: str | None = None
) -> Replay:
    """The code as it stands, with your facts and active patterns, replayed against
    the answers you accepted; `question` and `fact_key` name the failure it fixes."""
    knowledge = replace(knowledge_for(api), patterns=tuple(api.patterns("active")))
    key = normalize_answer_question(question or "")
    return replay_code(
        sightings_of(api.sightings()), knowledge, question_key=key or None, fact_key=fact_key,
        seed=key,
    )


def print_replay(replay: Replay) -> None:
    console.print(replay.summary(), markup=False)
    for outcome in replay.failures:
        console.print(
            f"  {outcome.sighting.question.strip()!r}: now {outcome.now!r}, "
            f"you accepted {outcome.sighting.answer!r}",
            markup=False,
        )


def run_replay(
    *, question: str | None = None, fact_key: str | None = None, json_line: bool = False
) -> int:
    """0 for a passing replay, 1 for a regression, 2 when the check could not run.

    `json_line` ends the output with the counts as one JSON line, which the
    fixer records as a kept fix's verification.
    """
    try:
        config, token = connected()
        api = WorkerApi(config, token)
        try:
            replay = code_replay(api, question=question, fact_key=fact_key)
        finally:
            api.close()
    except (httpx.HTTPError, LoginError, RuntimeError, ReferenceError) as error:
        console.print(f"Replay could not complete: {error}", markup=False)
        return 2
    print_replay(replay)
    if json_line:
        print(json.dumps(replay.verification() | {"unchecked": replay.unchecked}))
    return 0 if replay.passed else 1


def render_prose(items: list[dict[str, Any]]) -> None:
    view = table(f"Prose ({len(items)})")
    view.add_column("#", justify="right", style="dim")
    view.add_column("id", style="dim", no_wrap=True)
    view.add_column("question", overflow="ellipsis", ratio=1)
    view.add_column("written for", overflow="ellipsis")
    view.add_column("text", overflow="ellipsis", ratio=2)
    view.add_column("state", no_wrap=True)
    for index, item in enumerate(items, start=1):
        view.add_row(
            str(index),
            short_id(str(item.get("id", ""))),
            escape(str(item.get("question_label") or "-")),
            escape(str(item.get("company_name") or "-")),
            escape(" ".join(str(item.get("text") or "").split())),
            "approved" if item.get("approved") else "[yellow]pending[/yellow]",
        )
    console.print(view)


def run_prose(
    action: str | None,
    reference: str | None,
    *,
    question: str | None = None,
    file: str | None = None,
) -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        if action in {None, "list"}:
            items = api.prose()
            if not items:
                console.print("No prose yet. Long-form answers you submit land in the Inbox.")
                return 0
            render_prose(items)
            return 0
        if action == "add":
            if not question or not file:
                raise RuntimeError("'prose add' needs --question and --file")
            try:
                text = Path(file).expanduser().read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                raise RuntimeError(f"Prose file not found: {file}") from None
            if not text:
                raise RuntimeError("The prose file is empty")
            item = api.create_prose(question=question, text=text)
            console.print(f"Saved prose {short_id(str(item['id']))} for {question!r}, approved.")
            return 0
        if reference is None:
            raise RuntimeError(f"'prose {action}' needs a row from 'os hunt prose' or an id prefix")
        item = pick(api.prose(), reference, kind="prose")
        if action == "approve":
            api.update_prose(str(item["id"]), approved=True)
            console.print(f"Approved prose {short_id(str(item['id']))} for reuse.")
        elif action == "delete":
            api.delete_prose(str(item["id"]))
            console.print(f"Deleted prose {short_id(str(item['id']))}.")
        else:
            raise RuntimeError("Use: os hunt prose [list|add|approve|delete]")
    finally:
        api.close()
    return 0


def is_prose(row: dict[str, Any]) -> bool:
    return (
        str(row.get("reuse_policy")) == "contextual"
        or len(str(row.get("answer_text") or "")) >= PROSE_LENGTH
    )


def prose_markdown(rows: list[dict[str, Any]]) -> str:
    """The archive's long-form writing as one Markdown document, newest first."""
    lines = [
        "# Archived long-form answers",
        "",
        "Exported from `jobs_archive_answers`, the answer library frozen on 2026-10-14.",
        "Reference only; nothing reads this file.",
        "",
    ]
    for row in rows:
        question = " ".join(str(row.get("question") or row.get("normalized_key") or "").split())
        lines += [f"## {question}", "", str(row.get("answer_text") or "").strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def run_archive(section: str, *, markdown: str | None = None) -> int:
    if section == "rules":
        view = table(f"Retired rules ({len(RULES)}), translated to facts")
        view.add_column("rule", no_wrap=True)
        view.add_column("fact", no_wrap=True)
        view.add_column("formatter", no_wrap=True, style="dim")
        view.add_column("note", overflow="ellipsis", style="dim")
        for rule in RULES:
            view.add_row(rule.ref, rule.fact_key, rule.formatter or "", escape(rule.note))
        console.print(view)
        return 0
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        rows = api.archive_answers()
    finally:
        api.close()
    if section == "prose":
        writing = [row for row in rows if is_prose(row)]
        if markdown is None:
            console.print(
                f"{len(writing)} archived long-form answers; "
                "write them out with --markdown PATH."
            )
            return 0
        path = Path(markdown).expanduser()
        path.write_text(prose_markdown(writing), encoding="utf-8")
        console.print(f"Wrote {len(writing)} archived long-form answers to {path}")
        return 0
    view = table(f"Archived answers ({len(rows)}), frozen 2026-10-14")
    view.add_column("question", overflow="ellipsis", ratio=2)
    view.add_column("old answer", overflow="ellipsis", ratio=2)
    view.add_column("translates to", no_wrap=True)
    for row in rows:
        question = " ".join(str(row.get("question") or row.get("normalized_key") or "").split())
        translated = translate(question)
        view.add_row(
            escape(question),
            escape(" ".join(str(row.get("answer_text") or "").split())),
            translated.fact_key if translated else "[dim]-[/dim]",
        )
    console.print(view)
    return 0
