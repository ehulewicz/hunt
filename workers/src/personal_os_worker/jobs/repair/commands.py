"""`os hunt repair` and `os hunt validate`: the repair lane (design §10.3, §10.5).

`repair` takes every open failure that stopped at `format` or `strategy`, opens
its application in a new, isolated browser context guarded against submitting,
and retries only the failed field. What reads back is stored on the issue as a
candidate and the issue becomes `repaired`. With `--watch` it keeps draining the
queue while an apply session runs (`os hunt apply --repair` starts it), then
exits.

`validate` walks the repaired issues. For each it opens a visible page — an
unsubmitted application that showed the question, another posting first (R26) —
fills only that field with the candidate, outlines it, and asks. Accepting a
strategy makes it the control's preferred strategy in every later run;
accepting a format binds the question to the fact with that formatter.
Rejecting sends the issue back to the queue. Neither page can submit.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from datetime import date
from typing import Any

from playwright.sync_api import Error, Page

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.commands import connected
from personal_os_worker.core.service import ensure_chrome
from personal_os_worker.core.tables import ReferenceError, console
from personal_os_worker.jobs.autofill import FormFill
from personal_os_worker.jobs.browser import (
    OPERATIONS,
    Attempt,
    VisibleBrowser,
    fresh_control,
    inspect_fields,
    reveal_application_form,
    verify_control,
)
from personal_os_worker.jobs.formatters import render
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.repair.guards import install_guards
from personal_os_worker.jobs.repair.repairer import find_field, repair_on_page
from personal_os_worker.jobs.resolve import Knowledge

Ask = Callable[[str], str]
REPAIRABLE = frozenset({"format", "strategy"})
WATCH_SECONDS = 10.0


def knowledge_for(api: WorkerApi) -> Knowledge:
    try:
        career: dict[str, Any] | None = api.career()
    except RuntimeError:
        career = None
    return Knowledge.build(facts=api.facts(), career=career, today=date.today())


def queue(api: WorkerApi) -> list[dict[str, Any]]:
    return [
        issue
        for issue in api.autofill_failures()
        if issue.get("status") == "open" and issue.get("stopped_at") in REPAIRABLE
    ]


def repair_issue(
    api: WorkerApi, browser: VisibleBrowser, issue: dict[str, Any], knowledge: Knowledge
) -> bool:
    detail = api.autofill_failure(str(issue["id"]))
    url = next(
        (
            str(item["application_url"])
            for item in detail.get("occurrences") or []
            if item.get("application_url")
        ),
        None,
    )
    label = detail.get("field_label") or "?"
    if url is None:
        console.print(f"  skip   {label}: no application recorded for it")
        return False
    page = browser.isolated_page(url, guard=install_guards)
    try:
        reveal_application_form(page)
        candidate, note = repair_on_page(page, detail, knowledge)
    except Error as error:
        candidate, note = None, f"the page failed: {str(error).splitlines()[0][:120]}"
    finally:
        with contextlib.suppress(Error):
            page.context.close()
    if candidate is None:
        console.print(
            f"  open   {label}: {note}; 'os hunt apply failures diagnose' can look closer"
        )
        return False
    api.update_autofill_failure(str(issue["id"]), {"status": "repaired", "candidate": candidate})
    rule = candidate.get("strategy") if candidate["kind"] == "strategy" else candidate["formatter"]
    console.print(f"  [green]repaired[/green] {label}: {candidate['kind']} {rule}")
    return True


def alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def run_repair(*, watch: bool = False, until: int | None = None) -> int:
    config, token = connected()
    ensure_chrome(config.cdp_url)
    api = WorkerApi(config, token)
    attempted: set[str] = set()
    try:
        knowledge = knowledge_for(api)
        with VisibleBrowser(config.cdp_url) as browser:
            while True:
                pending = [item for item in queue(api) if str(item["id"]) not in attempted]
                for issue in pending:
                    attempted.add(str(issue["id"]))
                    repair_issue(api, browser, issue, knowledge)
                if not watch or (until is not None and not alive(until) and not pending):
                    break
                time.sleep(WATCH_SECONDS)
    finally:
        api.close()
    if not attempted and not watch:
        console.print("Nothing to repair: no open failure stopped at a format or a strategy.")
    return 0


def apply_candidate(
    page: Page, field: dict[str, Any], candidate: dict[str, Any], value: str
) -> bool:
    """Fill only this field with the candidate's strategy, and read it back."""
    fill = FormFill(index=int(field.get("index") or 0), kind="text", value=value)
    operation = OPERATIONS.get(str(candidate.get("strategy")))
    if operation is None:
        return False
    try:
        result = operation(Attempt(page, fresh_control(page, field), field, fill, None))
        return verify_control(page, field, result if result is not None else value)
    except (Error, RuntimeError):
        return False


def outline(page: Page, field: dict[str, Any]) -> None:
    with contextlib.suppress(Error, RuntimeError):
        fresh_control(page, field).evaluate(
            "element => { element.style.outline = '3px solid #22c55e';"
            " element.scrollIntoView({block: 'center'}); }"
        )


def bind_with_formatter(api: WorkerApi, candidate: dict[str, Any]) -> None:
    wanted = normalize_answer_question(str(candidate.get("question") or ""))
    question = next(
        (
            item
            for item in api.questions()
            if normalize_answer_question(str(item.get("sample_label") or "")) == wanted
        ),
        None,
    )
    if question is None:
        console.print("  the question was never recorded; bind it from the Inbox instead")
        return
    api.create_pattern(
        kind="binding",
        question_id=str(question["id"]),
        fact_key=str(candidate["fact_key"]),
        formatter=str(candidate["formatter"]),
        origin="inbox",
        status="active",
    )


def validate_issue(
    api: WorkerApi,
    browser: VisibleBrowser,
    issue: dict[str, Any],
    knowledge: Knowledge,
    *,
    ask: Ask,
) -> str:
    candidate = dict(issue.get("candidate") or {})
    label = issue.get("field_label") or candidate.get("question") or "?"
    try:
        target = api.validation_target(str(issue["id"]))
    except ReferenceError:
        console.print(f"  wait   {label}: no unsubmitted application shows it; try again later")
        return "skipped"
    rule = candidate.get("strategy" if candidate.get("kind") == "strategy" else "formatter")
    where = " (where it failed)" if target.get("original") else ""
    console.print(
        f"\n[bold]{label}[/bold]  {candidate.get('kind')} {rule}"
        f"  [dim]on {target['company_name']}, {target['title']}{where}[/dim]"
    )
    page = browser.open_page(str(target["application_url"]), guard=install_guards)
    reveal_application_form(page)
    field = find_field(inspect_fields(page), str(candidate.get("question") or label))
    if field is None:
        console.print("  the question is not on this form's first page; skipped")
        return "skipped"
    value = str(candidate.get("value") or "")
    if candidate.get("kind") == "format" and candidate.get("fact_key") in knowledge.facts:
        key = str(candidate["fact_key"])
        rendering = render(
            key,
            knowledge.facts[key],
            knowledge.types.get(key, "text"),
            field={"label": candidate.get("question")},
            question=str(candidate.get("question") or ""),
            prefer=str(candidate.get("formatter")),
        )
        value = rendering.value if rendering is not None else value
    took = apply_candidate(page, field, candidate, value)
    outline(page, field)
    console.print(f"  filled {value!r}, {'read back' if took else 'did NOT read back'}")
    while True:
        choice = ask("  [a]ccept  [r]eject  [s]kip  [q]uit: ").strip().casefold()[:1]
        if choice in {"a", "r", "s", "q"}:
            break
    if choice == "a":
        api.update_autofill_failure(str(issue["id"]), {"status": "validated"})
        if candidate.get("kind") == "format":
            bind_with_formatter(api, candidate)
        return "validated"
    if choice == "r":
        api.update_autofill_failure(str(issue["id"]), {"status": "open"})
        return "rejected"
    return "quit" if choice == "q" else "skipped"


def run_validate(*, ask: Ask = input) -> int:
    config, token = connected()
    ensure_chrome(config.cdp_url)
    api = WorkerApi(config, token)
    counts = {"validated": 0, "rejected": 0, "skipped": 0}
    try:
        repaired = [item for item in api.autofill_failures() if item.get("status") == "repaired"]
        if not repaired:
            console.print("Nothing to validate. 'os hunt repair' turns failures into candidates.")
            return 0
        knowledge = knowledge_for(api)
        with VisibleBrowser(config.cdp_url) as browser:
            for issue in repaired:
                outcome = validate_issue(api, browser, issue, knowledge, ask=ask)
                if outcome == "quit":
                    break
                counts[outcome] += 1
    finally:
        api.close()
    console.print(
        f"\n{counts['validated']} validated, {counts['rejected']} rejected, "
        f"{counts['skipped']} skipped. The pages stay open; nothing was submitted."
    )
    return 0
