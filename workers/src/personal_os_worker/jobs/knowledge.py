"""What the system knows, and what the old one knew.

`os hunt patterns` shows the loop's state: which questions fill
deterministically (`active`), and which are waiting in the Inbox (`candidate`).
`os hunt replay` replays the code as it stands against the answers you
accepted, the check a code change passes before it is kept. A fact set without
its replay (the web Library) waits pending until the next run or replay checks
it (`resolve_pending_facts`). `os hunt prose`
manages saved long-form writing. `os hunt archive` reads the frozen old system:
its answers, its retired rules with their translations, and its long-form
writing as Markdown.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from rich import box
from rich.markup import escape
from rich.table import Table

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.auth import LoginError
from personal_os_worker.core.commands import connected
from personal_os_worker.core.config import console, pick, short_id
from personal_os_worker.jobs import replay
from personal_os_worker.jobs.archive.lookup import translate
from personal_os_worker.jobs.archive.rules import RULES
from personal_os_worker.jobs.options import (
    expression_matches,
    normalize_answer_question,
)
from personal_os_worker.jobs.repair.commands import knowledge_for
from personal_os_worker.jobs.replay import Replay, replay_fact, sightings_of
from personal_os_worker.jobs.resolve import Knowledge

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


def _check(patterns: list[dict[str, Any]], questions: list[dict[str, Any]]) -> int:
    """Active expressions against every recorded question: where they disagree."""
    expressions: list[tuple[re.Pattern[str], dict[str, Any]]] = []
    for pattern in patterns:
        if pattern.get("kind") != "expression" or not pattern.get("expression"):
            continue
        try:
            compiled = re.compile(
                str(pattern["expression"]), re.IGNORECASE | re.DOTALL
            )
        except re.error:
            continue
        expressions.append((compiled, pattern))
    bindings = {
        normalize_answer_question(
            str(pattern.get("question_label") or pattern.get("question_key") or "")
        ): pattern
        for pattern in patterns
        if pattern.get("kind") == "binding"
    }
    multi: list[tuple[str, list[str], list[str]]] = []
    other: list[tuple[str, str, str, str]] = []
    for item in questions:
        label = str(item.get("sample_label") or item.get("label") or "").strip()
        if not label:
            continue
        hits = [
            (regex, pattern)
            for regex, pattern in expressions
            if expression_matches(regex, label)
        ]
        facts = {str(pattern["fact_key"]) for _, pattern in hits}
        if len(facts) > 1:
            multi.append(
                (label, sorted(facts), [str(p["expression"]) for _, p in hits])
            )
        bound = bindings.get(normalize_answer_question(label))
        if bound is None:
            continue
        bound_fact = str(bound["fact_key"])
        for _, pattern in hits:
            if str(pattern["fact_key"]) != bound_fact:
                other.append(
                    (label, bound_fact, str(pattern["expression"]),
                     str(pattern["fact_key"]))
                )
    if multi:
        view = Table(title="Answered by expressions of different facts")
        for header in ("Question", "Facts", "Expressions"):
            view.add_column(header)
        for label, names, found in multi:
            view.add_row(escape(label), ", ".join(names), escape(" ; ".join(found)))
        console.print(view)
    if other:
        view = Table(title="Match a question bound to another fact")
        for header in ("Question", "Bound to", "Expression -> fact"):
            view.add_column(header)
        for label, bound_fact, expression, fact in other:
            view.add_row(escape(label), bound_fact, escape(f"{expression} -> {fact}"))
        console.print(view)
    rows = len(multi) + len(other)
    console.print(
        f"{rows} conflict(s): {len(multi)} question(s) decided by different facts, "
        f"{len(other)} expression(s) matching a question bound elsewhere."
    )
    return 1 if rows else 0


def run_patterns(*, every: bool = False, check: bool = False) -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        statuses = (
            ("active", "candidate", "repaired", "validated", "rejected", "retired")
            if every
            else ("active", "candidate")
        )
        patterns = api.patterns(*statuses)
        recorded = api.questions() if check else []
    finally:
        api.close()
    if check:
        return _check(patterns, recorded)
    if not patterns:
        console.print(
            "Nothing learned yet. Apply to something; the Inbox ('os hunt inbox') is where "
            "proposals become patterns."
        )
        return 0
    render_patterns(patterns)
    return 0


def fact_replay(
    api: WorkerApi, key: str, value: Any, *, value_type: str | None = None
) -> Replay | None:
    """A new fact value, replayed against the answers you accepted; None when they
    cannot be read. The fact is yours either way: this only says what disagrees."""
    try:
        knowledge = replace(knowledge_for(api), patterns=tuple(api.patterns("active")))
        sightings = sightings_of(api.sightings())
    except (httpx.HTTPError, RuntimeError, ReferenceError):
        return None
    return replay_fact(sightings, knowledge, key, value, value_type=value_type)


def resolve_pending_facts(api: WorkerApi) -> int:
    """Replay every fact change still waiting for its check, and record what each
    found; how many now disagree with an answer you accepted.

    A value set without a replay (the web Library) is recorded pending. Each is
    replayed from the fact as it was just before it, so an accepted answer with
    no saved inputs still has its baseline. A deletion is never pending. When
    the answers cannot be read, every change stays pending for the next run.
    """
    try:
        pending = [change for change in api.changes(subject="fact", status="pending", limit=500)
                   if isinstance(change.get("after"), dict)]
        if not pending:
            return 0
        try:
            career: dict[str, Any] | None = api.career()
        except RuntimeError:
            career = None
        facts = api.facts()
        patterns = tuple(api.patterns("active"))
        sightings = sightings_of(api.sightings())
    except (httpx.HTTPError, RuntimeError, ReferenceError):
        return 0
    disagree = 0
    for change in reversed(pending):  # oldest first, as they were made
        key, before, after = str(change["subject_id"]), change.get("before"), change["after"]
        baseline = [fact for fact in facts if fact.get("key") != key]
        if isinstance(before, dict):
            baseline.append(before)
        knowledge = replace(
            Knowledge.build(facts=baseline, career=career, today=date.today()), patterns=patterns
        )
        replay = replay_fact(
            sightings, knowledge, key, after.get("value"), value_type=after.get("value_type")
        )
        try:
            api.resolve_change(str(change["id"]), replay.verification())
        except httpx.HTTPError:
            continue  # it stays pending; the next run checks it again
        disagree += not replay.passed
    return disagree


def report_fact_replay(key: str, replay: Replay | None) -> None:
    """Say what a fact change makes disagree, when anything does."""
    if replay is None or replay.passed:
        return
    console.print(f"{key}: {replay.summary()}", style="yellow", markup=False)
    for outcome in replay.failures[:5]:
        console.print(
            f"  {outcome.sighting.question.strip()!r}: now {outcome.now!r}, "
            f"you accepted {outcome.sighting.answer!r}",
            markup=False,
        )


def report_pending_facts(disagree: int) -> None:
    """One line when a fact set since the last check disagrees with your answers."""
    if disagree:
        console.print(
            f"{disagree} fact change(s) since the last check now disagree with answers you "
            "accepted; the Inbox lists them.",
            style="yellow", markup=False,
        )


def run_replay(
    *, question: str | None = None, fact_key: str | None = None, json_line: bool = False
) -> int:
    """0 for a passing replay, 1 for a regression, 2 when the check could not run.

    `json_line` ends the output with the counts as one JSON line, which the
    fixer records as a kept fix's verification. Otherwise the fact changes
    still pending are checked first; the fixer's check on scratch code writes
    nothing. The replay itself lives in `jobs/replay.py`.
    """
    if not json_line:
        try:
            config, token = connected()
            api = WorkerApi(config, token)
            try:
                report_pending_facts(resolve_pending_facts(api))
            finally:
                api.close()
        except (httpx.HTTPError, LoginError, RuntimeError, ReferenceError) as error:
            console.print(f"Replay could not complete: {error}", markup=False)
            return 2
    argv = ["--json"] if json_line else []
    if question:
        argv += ["--question", question]
    if fact_key:
        argv += ["--fact", fact_key]
    return replay.main(argv)


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


def run_prose(action: str | None, reference: str | None) -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        if action in {None, "list"}:
            items = api.prose()
            if not items:
                console.print(
                    "No stored prose. Runs no longer save long-form answers; "
                    "your writing lives in the narrative library (os hunt library get)."
                )
                return 0
            render_prose(items)
            return 0
        if reference is None:
            raise RuntimeError(f"'prose {action}' needs a row from 'os hunt prose' or an id prefix")
        item = pick(api.prose(), reference, kind="prose")
        if action == "delete":
            api.delete_prose(str(item["id"]))
            console.print(f"Deleted prose {short_id(str(item['id']))}.")
        else:
            raise RuntimeError("Use: os hunt prose [list|delete]")
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
